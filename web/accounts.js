"use strict";

// Konta = login i haslo; konfiguracja sesji dopiero w oknie uruchamiania.
function createAccountsView() {
  const view = {
    root: $("#view-accounts"), box: $("#account-list"), editor: $("#account-editor"), form: $("#account-form"),
    profiles: [], cards: new Map(), pending: new Set(), selected: new Set(), actionErrors: new Map(),
    editing: null, attachPid: null, dirty: false, inFlight: false, saving: false, importing: false,
    launching: false, launchIds: [], config: null, configAt: 0,

    async refresh() {
      if (this.inFlight) return;
      this.inFlight = true;
      try {
        this.profiles = (await getJSON("/api/autologin")).profiles;
        $("#account-load-error").hidden = true;
        if (!this.config || Date.now() - this.configAt > 10000) {
          try { this.config = await getJSON("/api/settings"); this.configAt = Date.now(); }
          catch { this.config = null; }
        }
        this.renderHealth(); this.render();
      } catch (e) {
        $("#account-load-error").textContent = `Odczyt kont: ${e.message}. Sprawdź panel.bat.`;
        $("#account-load-error").hidden = false;
      } finally { this.inFlight = false; }
    },
    ready() { return !!(hub.ok && this.config?.game_found && this.config?.admin); },
    client(p) { return hub.clients.find(c => c.pid === p.pid); },
    inGame(p) { return !!this.client(p) && (p.status?.state === "ingame" || !!this.client(p).snapshot?.ident?.nick); },
    busy(p) { return this.pending.has(p.id) || p.status?.state === "launch"
      || (!!this.client(p) && p.enabled && ["wait", "login", "select", "loading"].includes(p.status?.state)); },

    renderHealth() {
      const health = $("#account-health"), problems = [];
      if (!this.config) problems.push("Nie można sprawdzić gotowości gry.");
      else {
        if (!this.config.game_found) problems.push("Brak pliku gry — popraw ścieżkę w ustawieniach.");
        if (!this.config.admin) problems.push("Logowanie i bot wymagają uruchomienia panel.bat jako administrator.");
      }
      health.replaceChildren(); health.hidden = !problems.length;
      if (problems.length) {
        health.append(document.createTextNode(problems.join(" ") + " "));
        const link = el("a", null, "Ustawienia"); link.href = "#/ustawienia"; health.append(link);
      }
      $("#launch").disabled = !this.ready() || $("#launch").dataset.busy === "true";
      $("#account-launch-form button[type=submit]").disabled = !this.ready() || this.launching;
      this.updateSelection();
    },

    makeCard(id) {
      const row = el("tr"); row.dataset.id = id;
      row.innerHTML = `<td><input class="account-select" type="checkbox"></td><td><code class="account-login"></code></td>
        <td class="account-status-cell"><span class="al-state"></span><small class="account-problem" hidden></small></td>
        <td class="account-last"></td><td class="account-bot"></td>
        <td><div class="account-actions"><button class="btn account-run" type="button">Uruchom</button><a class="btn btn-quiet account-open">Panel</a><button class="btn btn-quiet account-bot-toggle" type="button">Bot</button><details class="account-more"><summary aria-label="Więcej opcji">⋯</summary><div class="account-extra"><button class="btn btn-quiet account-window" type="button">Pokaż grę</button><button class="btn btn-quiet account-edit" type="button">Edytuj login / hasło</button><button class="btn btn-quiet account-enabled" type="button">Wstrzymaj logowanie</button><button class="btn btn-danger account-remove" type="button">Usuń z panelu</button></div></details></div></td>`;
      const action = (selector, fn) => $(selector, row).addEventListener("click", () => {
        const p = this.profiles.find(p => p.id === id);
        if (p && !this.pending.has(id)) { $(".account-more", row).open = false; fn(p); }
      });
      $(".account-select", row).addEventListener("change", ev => {
        ev.target.checked ? this.selected.add(id) : this.selected.delete(id); this.updateSelection();
      });
      action(".account-run", p => this.openLaunch([p.id]));
      action(".account-edit", p => this.openEditor(p));
      action(".account-bot-toggle", p => this.toggleBot(p));
      action(".account-window", p => this.perform(p, () => post(`/api/clients/${p.pid}/window`, {visible:true})));
      action(".account-enabled", p => this.perform(p, () => post(`/api/autologin/${p.id}/save`, {enabled:!p.enabled})));
      action(".account-remove", p => {
        if (confirm(`Usunąć login „${p.login}” i zapisane hasło z panelu? Konto w grze pozostanie.`)) {
          this.perform(p, async () => {
            await post(`/api/autologin/${p.id}/remove`); if (this.editing === p.id) this.closeEditor();
          });
        }
      });
      return row;
    },

    render() {
      $("#account-count").textContent = `(${this.profiles.length})`;
      if (!this.profiles.length && !$(".empty-row", this.box)) {
        const row = el("tr", "empty-row"), td = el("td", null, "Brak kont. Wpisz login i hasło powyżej lub zaimportuj listę.");
        td.colSpan = 6; row.append(td); this.box.append(row);
      }
      if (this.profiles.length) $(".empty-row", this.box)?.remove();
      const seen = new Set();
      for (const p of this.profiles) {
        seen.add(p.id);
        let row = this.cards.get(p.id);
        if (!row) { row = this.makeCard(p.id); this.cards.set(p.id, row); this.box.append(row); }
        const c = this.client(p), state = p.status?.state, ingame = this.inGame(p), busy = this.busy(p), error = state === "error";
        $(".account-login", row).textContent = p.login;
        const select = $(".account-select", row); select.checked = this.selected.has(p.id); select.disabled = busy;
        select.setAttribute("aria-label", `Zaznacz ${p.login}`);
        const status = $(".al-state", row);
        status.textContent = error ? "Błąd" : ingame ? "W grze" : state === "launch" ? "Start…" : !c ? "Wyłączone"
          : !p.enabled ? "Ręcznie" : ({login:"Logowanie…", select:"Postać…", loading:"Mapa…"}[state] || "Oczekiwanie…");
        status.dataset.tone = error ? "bad" : ingame ? "ok" : busy ? "busy" : "";
        const problem = $(".account-problem", row), botError = ingame && ["busy", "error"].includes(c.radar);
        problem.textContent = this.actionErrors.get(p.id) || (error ? p.status.text : botError ? "Bot nie odpowiada. Otwórz Panel." : "");
        problem.hidden = !problem.textContent;
        $(".account-last", row).textContent = `CH${p.channel} / ${p.slot + 1}`;
        $(".account-bot", row).textContent = c?.radar === "on" ? "ON" : c?.radar === "starting" ? "Start…" : "OFF";
        const run = $(".account-run", row);
        run.textContent = busy ? "Czekaj…" : ingame ? "Przeloguj" : error ? "Ponów" : "Uruchom"; run.disabled = busy || !this.ready();
        const open = $(".account-open", row); open.hidden = !c; open.href = `#/p/${p.pid}`;
        const bot = $(".account-bot-toggle", row); bot.hidden = !ingame;
        bot.textContent = c?.radar === "on" ? "Bot OFF" : "Bot ON"; bot.disabled = busy || c?.radar === "starting" || !this.ready();
        $(".account-window", row).hidden = !c;
        for (const key of ["edit", "enabled", "remove", "window"]) $(".account-" + key, row).disabled = busy;
        $(".account-enabled", row).textContent = p.enabled ? "Wstrzymaj logowanie" : "Włącz logowanie";
      }
      for (const [id, row] of this.cards) if (!seen.has(id)) { row.remove(); this.cards.delete(id); this.selected.delete(id); }
      this.updateSelection();
    },

    updateSelection() {
      const available = this.profiles.filter(p => !this.busy(p)), selected = available.filter(p => this.selected.has(p.id));
      $("#account-selection").textContent = `Zaznaczone: ${selected.length}`;
      $("#account-run-selected").disabled = !selected.length || !this.ready() || this.launching;
      const all = $("#account-select-all"); all.checked = !!available.length && selected.length === available.length;
      all.indeterminate = selected.length > 0 && selected.length < available.length; all.disabled = !available.length;
    },

    openEditor(p = null, pid = null) {
      if (this.saving || (this.dirty && !confirm("Odrzucić wpisane dane konta?"))) return;
      this.editing = p?.id || null; this.attachPid = pid; this.dirty = false;
      this.form.reset(); this.form.elements.login.value = p?.login || "";
      this.form.elements.password.required = !p?.has_password; this.form.elements.password.type = "password";
      this.form.elements.password.placeholder = p?.has_password ? "puste = bez zmian" : "hasło";
      $("#account-show-password").textContent = "Pokaż"; $("#account-show-password").setAttribute("aria-pressed", "false");
      $("#account-editor-title").textContent = p ? "Edytuj konto" : pid ? "Przypisz konto" : "Dodaj konto";
      $("#account-save").textContent = p ? "Zapisz" : "Dodaj"; $("#account-cancel").hidden = !p && !pid;
      $("#account-password-note").hidden = !p?.has_password;
      $("#account-password-note").textContent = "Puste hasło = zachowaj zapisane. Po zmianie loginu podaj hasło.";
      $("#account-form-error").hidden = true; this.form.elements.login.focus(); this.editor.scrollIntoView({block:"nearest"});
    },

    closeEditor(focus = true) {
      if (this.saving) return;
      this.editing = null; this.attachPid = null; this.dirty = false; this.form.reset();
      this.form.elements.password.value = ""; this.form.elements.password.required = true;
      this.form.elements.password.type = "password"; this.form.elements.password.placeholder = "hasło";
      $("#account-show-password").textContent = "Pokaż"; $("#account-show-password").setAttribute("aria-pressed", "false");
      $("#account-editor-title").textContent = "Dodaj konto"; $("#account-save").textContent = "Dodaj";
      $("#account-cancel").hidden = true; $("#account-password-note").hidden = true; $("#account-form-error").hidden = true;
      if (!focus) { this.closeImport(); if (!this.launching) $("#account-launch-dialog").close(); }
      else this.form.elements.login.focus();
    },

    async save() {
      if (this.saving || !this.form.reportValidity()) return;
      this.saving = true; $("#account-form-error").hidden = true;
      const f = this.form.elements, body = {login:f.login.value.trim(), password:f.password.value};
      const url = this.attachPid ? `/api/clients/${this.attachPid}/autologin` : this.editing ? `/api/autologin/${this.editing}/save` : "/api/autologin";
      if (this.attachPid) body.enabled = false;
      const controls = Array.from(this.form.querySelectorAll("input,button")); controls.forEach(e => e.disabled = true); let saved;
      try { saved = (await post(url, body)).profile; f.password.value = ""; }
      catch (e) { $("#account-form-error").textContent = e.message; $("#account-form-error").hidden = false; }
      finally { this.saving = false; controls.forEach(e => e.disabled = false); }
      if (!saved) return;
      this.actionErrors.delete(saved.id); this.closeEditor();
      showNotice("Zapisano konto. Opcje wybierzesz przy uruchomieniu.", "ok"); await this.refresh();
    },

    closeImport() {
      if (this.importing) return;
      $("#account-import").hidden = true; $("#account-import-text").value = ""; $("#account-import-file").value = "";
      $("#account-import-toggle").setAttribute("aria-expanded", "false");
    },
    async importAccounts() {
      if (this.importing) return;
      this.importing = true;
      const controls = Array.from($("#account-import-form").querySelectorAll("input,textarea,button")), text = $("#account-import-text").value;
      controls.forEach(e => e.disabled = true); $("#account-import-status").textContent = "Importowanie…";
      try {
        const r = await post("/api/autologin/import", {text});
        $("#account-import-text").value = ""; $("#account-import-file").value = "";
        $("#account-import-status").textContent = `Dodano: ${r.added}. Pominięto istniejące / powtórzone: ${r.skipped}.`; await this.refresh();
      } catch (e) { $("#account-import-status").textContent = `Import nie został zapisany: ${e.message}`; }
      finally { this.importing = false; controls.forEach(e => e.disabled = false); }
    },

    openLaunch(ids) {
      if (this.launching) return;
      this.launchIds = ids.filter(id => this.profiles.some(p => p.id === id && !this.busy(p))); if (!this.launchIds.length) return;
      const p = this.profiles.find(p => p.id === this.launchIds[0]), f = $("#account-launch-form").elements;
      f.channel.value = String(this.launchIds.length === 1 ? p.channel : 1); f.slot.value = String(this.launchIds.length === 1 ? p.slot : 0);
      f.radar.checked = this.launchIds.length === 1 && !!p.radar; f.restart.checked = this.launchIds.length === 1 && !!p.restart;
      $("#account-launch-target").textContent = this.launchIds.length === 1 ? p.login : `Wybrane konta: ${this.launchIds.length}. Opcje wspólne dla wszystkich.`;
      $("#account-launch-error").hidden = true; this.renderHealth(); $("#account-launch-dialog").showModal();
    },
    async submitLaunch() {
      if (this.launching || !this.ready()) return;
      const f = $("#account-launch-form").elements;
      const options = {channel:Number(f.channel.value), slot:Number(f.slot.value), radar:f.radar.checked, restart:f.restart.checked};
      const ids = [...this.launchIds]; let successes = 0;
      this.launching = true; ids.forEach(id => {this.pending.add(id);this.actionErrors.delete(id);}); this.renderHealth(); this.render();
      $("#account-launch-cancel").disabled = true;
      try {
        for (const id of ids) {
          const p = this.profiles.find(p => p.id === id); if (!p) continue;
          try { await post(`/api/autologin/${id}/launch`, {...options, relog:this.inGame(p)}); successes++; }
          catch (e) { this.actionErrors.set(id, e.message); }
        }
      } finally {
        ids.forEach(id => this.pending.delete(id)); this.launching = false;
        $("#account-launch-cancel").disabled = false; $("#account-launch-dialog").close();
        showNotice(`Zlecono uruchomienie / logowanie: ${successes}/${ids.length}.` + (successes < ids.length ? " Błędy są w tabeli." : ""), successes === ids.length ? "ok" : "bad");
        await hub.refresh(); await this.refresh();
      }
    },

    async perform(p, fn) {
      if (this.pending.has(p.id)) return;
      this.pending.add(p.id); this.actionErrors.delete(p.id); this.render();
      try { await fn(); } catch (e) { this.actionErrors.set(p.id, e.message); showNotice(e.message); }
      finally { this.pending.delete(p.id); await hub.refresh(); await this.refresh(); }
    },
    async toggleBot(p) {
      const c = this.client(p); if (!c) return; const start = c.radar !== "on";
      await this.perform(p, async () => {
        await post(`/api/autologin/${p.id}/save`, {radar:start}); await post(`/api/clients/${p.pid}/radar/${start ? "start" : "stop"}`);
      });
    },
  };
  view.form.addEventListener("input", () => {view.dirty = true;});
  view.form.addEventListener("submit", ev => {ev.preventDefault();view.save();});
  $("#account-cancel").addEventListener("click", () => view.closeEditor());
  $("#account-show-password").addEventListener("click", () => {
    const input = view.form.elements.password; input.type = input.type === "password" ? "text" : "password";
    $("#account-show-password").textContent = input.type === "text" ? "Ukryj" : "Pokaż"; $("#account-show-password").setAttribute("aria-pressed", String(input.type === "text"));
    $("#account-show-password").setAttribute("aria-label", input.type === "text" ? "Ukryj hasło" : "Pokaż hasło");
  });
  $("#account-import-toggle").addEventListener("click", () => {
    if (view.importing) return;
    if (!$("#account-import").hidden) view.closeImport();
    else {$("#account-import").hidden = false; $("#account-import-toggle").setAttribute("aria-expanded", "true"); $("#account-import-status").textContent = ""; $("#account-import-text").focus();}
  });
  $("#account-import-cancel").addEventListener("click", () => view.closeImport());
  $("#account-import-form").addEventListener("submit", ev => {ev.preventDefault();view.importAccounts();});
  $("#account-import-file").addEventListener("change", async ev => {
    const file = ev.target.files[0]; if (!file) return;
    try {
      if (file.size > 256 * 1024) throw new Error("Maksymalny rozmiar pliku: 256 KB.");
      $("#account-import-text").value = await file.text(); $("#account-import-status").textContent = "Plik wczytany. Kliknij Importuj konta.";
    } catch (e) {$("#account-import-status").textContent = e.message;}
  });
  $("#account-select-all").addEventListener("change", ev => {
    view.profiles.filter(p => !view.busy(p)).forEach(p => ev.target.checked ? view.selected.add(p.id) : view.selected.delete(p.id)); view.render();
  });
  $("#account-run-selected").addEventListener("click", () => view.openLaunch([...view.selected]));
  $("#account-launch-cancel").addEventListener("click", () => {if (!view.launching) $("#account-launch-dialog").close();});
  $("#account-launch-dialog").addEventListener("cancel", ev => {if (view.launching) ev.preventDefault();});
  $("#account-launch-form").addEventListener("submit", ev => {ev.preventDefault();view.submitLaunch();});
  return view;
}
