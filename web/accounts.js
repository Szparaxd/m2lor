"use strict";

// Konta sa niezalezne od procesow gry. Polling aktualizuje tylko karty, nigdy formularz.
function createAccountsView() {
  const view = {
    root: $("#view-accounts"), box: $("#account-list"), editor: $("#account-editor"), form: $("#account-form"),
    profiles: [], cards: new Map(), pending: new Set(), actionErrors: new Map(), editing: null, attachPid: null,
    inFlight: false, saving: false, config: null, configAt: 0,

    async refresh() {
      if (this.inFlight) return;
      this.inFlight = true;
      try {
        const data = await getJSON("/api/autologin");
        this.profiles = data.profiles;
        $("#account-load-error").hidden = true;
        if (!this.config || Date.now() - this.configAt > 10000) {
          try {
            this.config = await getJSON("/api/settings");
            this.configAt = Date.now();
          } catch { this.config = null; }
        }
        this.renderHealth();
        this.render();
      } catch (e) {
        $("#account-load-error").textContent = `Nie udało się odczytać kont: ${e.message}. Sprawdź, czy panel.bat jest uruchomiony.`;
        $("#account-load-error").hidden = false;
      } finally { this.inFlight = false; }
    },

    renderHealth() {
      const health = $("#account-health");
      health.replaceChildren();
      const problems = [];
      if (!this.config) problems.push("Nie udało się sprawdzić gotowości gry. Spróbuj ponownie za chwilę.");
      else {
        if (!this.config.game_found) problems.push("Nie znaleziono pliku gry. Wskaż poprawny plik .exe w ustawieniach.");
        if (!this.config.admin) problems.push("Uruchom panel.bat jako administrator, aby działało automatyczne logowanie i bot.");
      }
      health.hidden = !problems.length;
      for (const text of problems) health.append(el("p", null, text));
      if (problems.length) {
        const link = el("a", null, "Otwórz ustawienia gry →");
        link.href = "#/ustawienia";
        health.append(link);
      }
      $("#launch").disabled = !this.ready() || $("#launch").dataset.busy === "true";
      $("#account-save-launch").disabled = this.saving || $("#account-save-launch").hidden || !this.ready();
    },

    ready() { return !!(hub.ok && this.config && this.config.game_found && this.config.admin); },

    makeCard(id) {
      const card = el("article", "card account");
      card.dataset.id = id;
      card.innerHTML = `<div class="account-head"><div><h2 class="account-name"></h2><p class="account-meta"></p></div><span class="al-state" role="status"></span></div>
        <p class="account-bot hint"></p><ol class="login-progress" aria-label="Przebieg logowania" hidden><li>Uruchomienie gry</li><li>Logowanie</li><li>Wybór postaci</li><li>W grze</li></ol>
        <p class="account-problem" role="status" hidden></p><p class="account-running-note hint" hidden></p>
        <div class="account-actions"><button class="btn btn-gold account-run" type="button">Uruchom i zaloguj</button><a class="btn btn-gold account-open">Panel postaci</a><button class="btn account-bot-toggle" type="button">Włącz bota</button><button class="btn btn-quiet account-window" type="button">Pokaż okno gry</button><button class="btn btn-quiet account-edit" type="button">Edytuj konto</button></div>
        <details class="account-more"><summary>Więcej opcji</summary><div class="account-extra"><button class="btn btn-quiet account-relog" type="button">Przeloguj na zapisany kanał i postać</button><button class="btn btn-quiet account-enabled" type="button">Wstrzymaj autologowanie</button><button class="btn btn-danger account-remove" type="button">Usuń zapisane konto</button></div></details>`;
      const action = (selector, fn) => $(selector, card).addEventListener("click", () => {
        const p = this.profiles.find(p => p.id === id);
        if (p && !this.pending.has(id)) fn(p);
      });
      action(".account-run", p => this.launch(p));
      action(".account-edit", p => this.openEditor(p));
      action(".account-bot-toggle", p => this.toggleBot(p));
      action(".account-window", p => this.perform(p, async () => {
        await post(`/api/clients/${p.pid}/window`, { visible: true });
      }));
      action(".account-relog", p => {
        if (confirm("Wylogować postać i zalogować ją ponownie na zapisany kanał i pozycję postaci?")) {
          this.perform(p, async () => { await post(`/api/clients/${p.pid}/relog`); });
        }
      });
      action(".account-enabled", p => this.perform(p, async () => {
        await post(`/api/autologin/${p.id}/save`, { enabled: !p.enabled });
      }));
      action(".account-remove", p => {
        if (!confirm(`Usunąć zapisane konto „${p.nickname || p.login}” z panelu? Usuwa to dane logowania, nie konto w grze.`)) return;
        this.perform(p, async () => {
          await post(`/api/autologin/${p.id}/remove`);
          if (this.editing === p.id) this.closeEditor();
        });
      });
      return card;
    },

    render() {
      $("#account-count").textContent = plural(this.profiles.length, "konto", "konta", "kont");
      if (!this.profiles.length) {
        if (!$(".empty", this.box)) {
          const empty = el("div", "empty");
          empty.append(el("strong", null, "Dodaj swoje pierwsze konto"),
            el("p", null, "Wpisz login i hasło tutaj, w panelu. Gra nie musi być uruchomiona."));
          const add = el("button", "btn btn-gold", "+ Dodaj konto");
          add.type = "button";
          add.addEventListener("click", () => this.openEditor());
          empty.append(add);
          this.box.append(empty);
        }
      } else $(".empty", this.box)?.remove();
      const seen = new Set();
      for (const p of this.profiles) {
        seen.add(p.id);
        let card = this.cards.get(p.id);
        if (!card) { card = this.makeCard(p.id); this.cards.set(p.id, card); this.box.append(card); }
        const c = hub.clients.find(c => c.pid === p.pid);
        const state = p.status?.state || "idle";
        const ingame = !!c && (state === "ingame" || !!c.snapshot?.ident?.nick);
        const busy = this.pending.has(p.id) || state === "launch" || (!!c && ["wait", "login", "select", "loading"].includes(state));
        const error = state === "error";
        const disabled = this.pending.has(p.id);
        $(".account-name", card).textContent = p.nickname || p.login;
        $(".account-meta", card).textContent = `${p.login} · CH${p.channel} · Postać ${p.slot + 1}`;
        const status = $(".al-state", card);
        status.textContent = error ? "Wymaga uwagi" : ingame ? "W grze" : state === "launch" ? "Uruchamiam grę…"
          : !c ? "Wyłączone" : !p.enabled ? "Logowanie ręczne" : ({login:"Loguję…",select:"Wybieram postać…",loading:"Ładuję mapę…"}[state] || "Przygotowuję grę…");
        status.dataset.tone = error ? "bad" : ingame ? "ok" : busy ? "busy" : "";
        $(".account-bot", card).textContent = ingame
          ? `Bot: ${c.radar === "on" ? "włączony" : c.radar === "starting" ? "uruchamia się…" : "wyłączony"}`
          : `Bot po zalogowaniu: ${p.radar ? "włączony" : "wyłączony"}`;
        const progress = $(".login-progress", card);
        progress.hidden = !busy && !ingame;
        const index = ingame ? 3 : state === "loading" || state === "select" ? 2 : state === "login" ? 1 : 0;
        Array.from(progress.children).forEach((li, i) => {
          li.dataset.step = i < index ? "done" : i === index ? "current" : "next";
          if (i === index) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
        });
        const problem = $(".account-problem", card);
        const botError = ingame && ["error", "busy"].includes(c.radar);
        problem.hidden = !error && !botError && !this.actionErrors.has(p.id);
        problem.textContent = this.actionErrors.get(p.id) || (error ? `${p.status.text}. Popraw dane w „Edytuj konto” lub spróbuj ponownie.`
          : botError ? "Bot nie odpowiada. Otwórz panel postaci lub spróbuj uruchomić go ponownie." : "");
        const run = $(".account-run", card);
        run.hidden = !!c && !error && p.enabled;
        run.disabled = busy || disabled || !this.ready();
        run.textContent = error ? "Spróbuj ponownie" : busy ? "Uruchamianie…" : c ? "Wznów logowanie" : "Uruchom i zaloguj";
        const open = $(".account-open", card);
        open.hidden = !ingame;
        open.href = `#/p/${p.pid}`;
        const bot = $(".account-bot-toggle", card);
        bot.hidden = !ingame;
        bot.textContent = c?.radar === "on" ? "Wyłącz bota" : c?.radar === "starting" ? "Bot startuje…" : "Włącz bota";
        bot.disabled = disabled || c?.radar === "starting" || !this.ready();
        $(".account-window", card).hidden = !c;
        $(".account-window", card).disabled = disabled;
        $(".account-edit", card).disabled = busy || disabled;
        $(".account-relog", card).hidden = !ingame || !p.enabled;
        $(".account-relog", card).disabled = disabled;
        $(".account-enabled", card).textContent = p.enabled ? "Wstrzymaj autologowanie" : "Włącz autologowanie";
        $(".account-enabled", card).disabled = busy || disabled;
        $(".account-remove", card).disabled = busy || disabled;
        const note = $(".account-running-note", card);
        note.hidden = !(c && ingame && p.enabled);
        note.textContent = "Zmianę kanału lub postaci zastosujesz przez „Więcej opcji” → „Przeloguj”.";
      }
      for (const [id, card] of this.cards) if (!seen.has(id)) { card.remove(); this.cards.delete(id); }
    },

    openEditor(profile = null, pid = null) {
      if (this.saving) return;
      if (!this.editor.hidden && !confirm("Odrzucić zmiany w otwartym formularzu konta?")) return;
      this.editing = profile?.id || null;
      this.attachPid = pid;
      this.form.reset();
      const f = this.form.elements;
      for (const key of ["nickname", "login"]) f[key].value = profile?.[key] || "";
      f.channel.value = String(profile?.channel || 1);
      f.slot.value = String(profile?.slot || 0);
      for (const key of ["radar", "enabled", "restart"]) f[key].checked = profile ? !!profile[key] : key !== "restart";
      f.password.required = !profile?.has_password;
      f.password.placeholder = profile?.has_password ? "Zostaw puste, aby zachować hasło" : "Wpisz hasło konta";
      f.password.type = "password";
      $("#account-show-password").textContent = "Pokaż";
      $("#account-show-password").setAttribute("aria-pressed", "false");
      $("#account-show-password").setAttribute("aria-label", "Pokaż hasło");
      $("#account-editor-title").textContent = profile ? "Edytuj konto" : "Dodaj konto";
      $("#account-editor-note").textContent = pid ? "Dane przypiszemy do wybranego, działającego klienta." : "Dane wpisujesz tutaj, zanim uruchomisz grę.";
      $("#account-password-note").textContent = profile?.has_password
        ? "Hasło jest zapisane. Zmieniając login, podaj również hasło."
        : "Hasło zapisujemy zaszyfrowane dla Twojego konta Windows.";
      $("#account-save").textContent = profile ? "Zapisz zmiany" : "Zapisz konto";
      $("#account-save-launch").hidden = !!profile || !!pid;
      this.renderHealth();
      $("#account-form-error").hidden = true;
      this.editor.hidden = false;
      f.nickname.focus();
      this.editor.scrollIntoView({block:"nearest"});
    },

    closeEditor(focus = true) {
      if (this.saving) return;
      this.form.elements.password.value = "";
      this.editor.hidden = true;
      this.editing = null;
      this.attachPid = null;
      if (focus) $("#account-add").focus();
    },

    async save(launch) {
      if (this.saving) return;
      launch = !!(launch && !this.editing && !this.attachPid);
      const f = this.form.elements;
      if (!this.form.reportValidity()) return;
      if (launch && !this.ready()) { this.formError("Sprawdź gotowość gry powyżej formularza."); return; }
      this.saving = true;
      $("#account-form-error").hidden = true;
      const buttons = Array.from(this.editor.querySelectorAll("button"));
      buttons.forEach(b => b.disabled = true);
      let saved = null;
      try {
        const body = {nickname:f.nickname.value.trim(), login:f.login.value.trim(), password:f.password.value,
          channel:Number(f.channel.value), slot:Number(f.slot.value), radar:f.radar.checked,
          enabled:launch ? true : f.enabled.checked, restart:f.restart.checked};
        const url = this.attachPid ? `/api/clients/${this.attachPid}/autologin`
          : this.editing ? `/api/autologin/${this.editing}/save` : "/api/autologin";
        saved = (await post(url, body)).profile;
        f.password.value = "";
      } catch (e) { this.formError(e.message); }
      finally {
        this.saving = false;
        buttons.forEach(b => b.disabled = false);
        this.renderHealth();
      }
      if (!saved) return;
      this.actionErrors.delete(saved.id);
      this.closeEditor();
      showNotice("Konto zapisane.", "ok");
      await this.refresh();
      if (launch) await this.launch(saved);
    },

    formError(text) { $("#account-form-error").textContent = text; $("#account-form-error").hidden = false; },

    async perform(p, fn) {
      if (this.pending.has(p.id)) return;
      this.actionErrors.delete(p.id);
      this.pending.add(p.id);
      this.render();
      try { await fn(); }
      catch (e) {
        this.actionErrors.set(p.id, e.message);
        showNotice(e.message);
        const card = this.cards.get(p.id);
        if (card) { const problem = $(".account-problem", card); problem.textContent = e.message; problem.hidden = false; }
      } finally {
        this.pending.delete(p.id);
        await hub.refresh();
        await this.refresh();
      }
    },

    async launch(p) {
      if (!this.ready()) { showNotice("Sprawdź plik gry i uruchom panel.bat jako administrator."); return; }
      await this.perform(p, async () => {
        await post(`/api/autologin/${p.id}/launch`);
        showNotice(`Uruchamiam i loguję konto „${p.nickname || p.login}”.`, "ok");
      });
    },

    async toggleBot(p) {
      const c = hub.clients.find(c => c.pid === p.pid);
      if (!c) return;
      const start = c.radar !== "on";
      await this.perform(p, async () => {
        // Trwale ustawienie zapobiega samoczynnemu wznowieniu zatrzymanego bota przez petle logowania.
        await post(`/api/autologin/${p.id}/save`, {radar:start});
        await post(`/api/clients/${p.pid}/radar/${start ? "start" : "stop"}`);
        showNotice(start ? "Uruchamiam bota." : "Bot zatrzymany.", "ok");
      });
    },
  };
  $("#account-add").addEventListener("click", () => view.openEditor());
  $("#account-cancel").addEventListener("click", () => view.closeEditor());
  $("#account-show-password").addEventListener("click", () => {
    const input = view.form.elements.password;
    input.type = input.type === "password" ? "text" : "password";
    const shown = input.type === "text";
    $("#account-show-password").textContent = shown ? "Ukryj" : "Pokaż";
    $("#account-show-password").setAttribute("aria-pressed", String(shown));
    $("#account-show-password").setAttribute("aria-label", shown ? "Ukryj hasło" : "Pokaż hasło");
  });
  view.form.addEventListener("submit", ev => { ev.preventDefault(); view.save(ev.submitter?.value === "launch"); });
  return view;
}
