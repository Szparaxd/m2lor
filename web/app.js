// Panel bota (recon/hub.py) – cała obsługa bota w przeglądarce, bez okien radaru.
// Widoki (hash):
//   #/            Postacie: wszystkie klienty gry (tabliczki jak lewa kolumna m2bob),
//   #/p/<pid>     jedna postać: radar na canvasie + wszystkie funkcje + narzędzia radaru,
//   #/konta       profile autologowania,
//   #/ustawienia  ścieżka gry, tryb radaru, pliki konfiguracji (vnum mikstur, peleryn, lista potworów),
//   #/dziennik    logi radarów / uruchamiania / logowania.
// Lista klientów jest odpytywana raz na sekundę dla wszystkich widoków (szyna po lewej też z niej korzysta).
// Elementy są aktualizowane w miejscu (bez przebudowy DOM), żeby odświeżanie nie gubiło fokusu i kliknięć.
"use strict";

const LIST_POLL_MS = 1000;
const DETAIL_POLL_MS = 250;
const LOG_POLL_MS = 2000;
const CLASS_PL = { warrior: "Wojownik", assassin: "Ninja", sura: "Sura", shaman: "Szaman", wolfman: "Lykan" };
const CLASS_PL_W = { warrior: "Wojowniczka", assassin: "Ninja", sura: "Sura", shaman: "Szamanka", wolfman: "Lykanka" };
// szybkie przełączniki na tabliczce listy (pokazywane, gdy radar ma daną opcję)
const SWITCHES = [["autopot", "Mikstura"], ["autoloot", "Loot"], ["farm", "Farma"], ["area_atk", "Atak"],
  ["autowalk", "Chodzenie"], ["autobuff", "Buffy"]];
const RADAR_STATE = {
  on: "działa", off: "nie działa", starting: "uruchamia się (kalibracja trwa kilka sekund)",
  busy: "nie odpowiada", error: "zwrócił błąd",
};
// zakładki widoku postaci: [tytuł, opcje radaru, stan funkcji ze snapshotu]
const TABS = [
  ["Mikstura", ["autopot", "pot_low", "pot_high", "pot_cd", "pot_sizes"],
    (s) => `Wypito ${s.status.pot_count}. Picie ${s.status.pot_mode}` +
      (s.status.pot_vnums && s.status.pot_vnums.length ? `, z ekwipunku (vnum ${s.status.pot_vnums.join(", ")}).` : ", slotem paska.")],
  ["Loot", ["autoloot", "loot_mode", "loot_interval", "loot_radius"],
    (s) => `Podniesiono ${s.status.loot_count}. Na ziemi w zasięgu radaru: ${((s.overlay && s.overlay.ground) || []).length}.`],
  ["Farma", ["farm", "farm_radius", "farm_metin", "farm_boss", "prep_dismount", "prep_buffs", "prep_remount"],
    (s) => (s.status.combat ? `Walka: ${s.status.combat}.` : "Brak walki.") + ` Metinów na mapie: ${s.status.metins}.`],
  ["Atak pakietem", ["area_atk", "area_atk_radius", "area_atk_mode", "area_atk_hop"],
    (s) => (s.status.area_atk ? `${s.status.area_atk}.` : "Wyłączony.") + ` Wysłano paczek: ${s.status.area_atk_count}.`],
  ["Peleryna", ["cape", "cape_period", "cape_min_mobs"],
    (s) => (s.status.cape ? `${s.status.cape}.` : "Wyłączona.") + ` Użyto: ${s.status.cape_count}. Vnum peleryn ustawisz w Ustawieniach.`],
  ["Chodzenie", ["autowalk", "walk_fight"],
    (s) => `Trasa: ${plural(s.status.waypoints, "punkt", "punkty", "punktów")}. Punkty dodajesz klikając na radarze.`],
  ["Buffy", ["autobuff"], (s) => (s.status.buff ? `${s.status.buff}.` : "Wyłączone.") + ` Użyto: ${s.status.buff_count}.`],
  ["Speedhack", ["speed", "move_mult", "atk_mult"], () => "Test: serwer powinien ucinać zbyt szybki ruch."],
];
const FOLLOW_TAB = "Podążanie";
const OPT = {
  autopot: "Auto-mikstura",
  pot_low: ["Pij poniżej", "% HP"],
  pot_high: ["Lecz do", "% HP"],
  pot_cd: ["Odstęp między miksturami", "s", 0.05],
  pot_sizes: "Rozmiary mikstur",
  autoloot: "Auto-loot",
  loot_mode: "Sposób zbierania",
  loot_interval: ["Zbieraj co", "s", 0.1],
  loot_radius: ["Zasięg zbierania", "", 100],
  farm: "Auto-farma w okręgu wokół postaci",
  farm_radius: ["Promień farmy", "", 100],
  farm_metin: "Najpierw bij metiny",
  farm_boss: "Najpierw bij bossy",
  prep_dismount: "Przed bossem zsiądź z konia",
  prep_buffs: "Przed bossem użyj Aury i Szału",
  prep_remount: "Po walce z bossem wsiądź na konia",
  area_atk: "Masowy atak pakietem (bez animacji i ruchu)",
  area_atk_radius: ["Zasięg ataku", "", 100],
  area_atk_mode: "Tryb ataku",
  area_atk_hop: "Przeskakuj do dalekich potworów",
  cape: "Używaj peleryny przy potworach",
  cape_period: ["Co ile", "s", 5],
  cape_min_mobs: ["Najmniej potworów w pobliżu", ""],
  autowalk: "Auto-chodzenie po trasie",
  walk_fight: "W drodze bij metiny i bossy",
  autobuff: "Auto-buff",
  speed: "Speedhack",
  move_mult: ["Mnożnik ruchu", "×", 0.1],
  atk_mult: ["Mnożnik ataku", "×", 0.1],
};
const CHOICE_PL = {
  human: "Jak gracz", instant: "Naraz w zasięgu", high: "Wszystko widoczne",
  safe: "Realistyczny", aggro: "Agresywny",
};
const ACTION_PL = {
  reinject: "Wstrzyknięcie DLL", calibrate: "Kalibracja", rescan: "Skan potworów", rebuild_map: "Budowa mapy",
};
// stan pętli autologowania (recon/autologin.py) -> kolor znacznika
const AL_STATE_OK = new Set(["ingame"]);
const AL_STATE_BAD = new Set(["error", "off"]);
const POT_SIZE_PL = { D: "Duża", "Ś": "Średnia", M: "Mała" };
const KIND_COLOR = {
  mob: "#e2553f", boss: "#c46be0", king: "#f0a03a", special: "#3fcfb4",
  metin: "#f2cf4a", player: "#f4efe6", npc: "#5b9be0",
};

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const notice = $("#notice");
let noticeTimer = null;

function showNotice(text, tone = "bad") {
  notice.textContent = text;
  notice.dataset.tone = tone;
  notice.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice.hidden = true; }, 6000);
}

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.ok === false) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function plural(n, one, few, many) {
  if (n === 1) return `${n} ${one}`;
  const d = n % 10, t = n % 100;
  return `${n} ${d >= 2 && d <= 4 && (t < 12 || t > 14) ? few : many}`;
}

const mapLabel = (name) => (name ? name.replace(/^metin2_map_/, "mapa ") : "mapa nieznana");

function className(id) {
  return id.cls ? (id.sex === "w" ? CLASS_PL_W : CLASS_PL)[id.cls] || id.cls : null;
}

function setBar(bar, cur, max) {
  const ok = Number.isFinite(cur) && Number.isFinite(max) && max > 0;
  $(".fill", bar).style.width = ok ? `${Math.max(0, Math.min(100, (100 * cur) / max))}%` : "0";
  $(".num", bar).textContent = ok ? `${cur} / ${max}` : "–";
}

function setFace(img, id) {
  const face = id && id.face ? `/faces/${id.face}` : "";
  if (img.getAttribute("src") !== face) face ? img.setAttribute("src", face) : img.removeAttribute("src");
  img.parentElement.classList.toggle("is-empty", !face);
}

// wspólne dla tabliczki i nagłówka widoku postaci: portret, nick, opis, paski, przyciski
function renderIdentity(root, c, s, nickTarget) {
  const id = (s && s.ident) || {};
  setFace($(".portrait img", root), id);
  nickTarget.textContent = id.nick || (c.radar === "on" ? "Postać poza światem" : "Klient gry bez bota");
  $(".meta", root).textContent = s
    ? [className(id), id.level ? `poziom ${id.level}` : null, mapLabel(s.map)].filter(Boolean).join(", ")
    : "Uruchom bota, gdy postać będzie już w świecie gry.";
  $(".bars", root).hidden = !s;
  if (s) {
    setBar($(".bar-hp", root), s.hp.cur, s.hp.max);
    setBar($(".bar-sp", root), s.sp.cur, s.sp.max);
  }
  const win = $(".act-window", root);
  win.dataset.visible = String(c.window_visible);
  win.textContent = c.window_visible ? "Ukryj okno gry" : "Pokaż okno gry";
  win.hidden = c.radar === "gone";
  const radar = $(".act-radar", root);
  radar.dataset.state = c.radar;
  radar.textContent = c.radar === "on" ? "Zatrzymaj bota" : c.radar === "starting" ? "Bot startuje…" : "Uruchom bota";
  radar.disabled = c.radar === "starting";
  radar.hidden = c.radar === "gone";
  radar.className = `btn act-radar${c.radar === "on" ? " btn-quiet" : " btn-gold"}`;
}

function wireActions(root, getPid, afterChange) {
  $(".act-window", root).addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await post(`/api/clients/${getPid()}/window`, { visible: btn.dataset.visible !== "true" });
      await afterChange();
    } catch (e) { showNotice(`Okno gry: ${e.message}`); }
    btn.disabled = false;
  });
  $(".act-radar", root).addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const starting = btn.dataset.state !== "on";
    btn.disabled = true;
    try {
      await post(`/api/clients/${getPid()}/radar/${starting ? "start" : "stop"}`);
      await afterChange();
    } catch (e) {
      showNotice(`${starting ? "Uruchomienie" : "Zatrzymanie"} bota: ${e.message}`);
      btn.disabled = false;
    }
  });
}

async function sendOption(pid, name, value) {
  const res = await post(`/api/clients/${pid}/command`, { name, value });
  return res.options;
}

/* ======================= wspólny stan: lista klientów ======================= */
const hub = {
  clients: [],
  ok: false,
  inFlight: false,

  async refresh() {
    if (this.inFlight) return;
    this.inFlight = true;
    try {
      this.clients = (await getJSON("/api/clients")).clients;
      this.ok = true;
    } catch {
      this.ok = false;
    } finally {
      this.inFlight = false;
    }
    rail.render();
  },
};

/* ======================= szyna: postacie w skrócie ======================= */
const rail = {
  list: $("#rail-list"),
  state: $("#hub-state"),
  items: new Map(),

  render() {
    this.state.textContent = hub.ok ? "Hub działa · 127.0.0.1" : "Brak połączenia z hubem. Uruchom panel.bat.";
    this.state.dataset.tone = hub.ok ? "ok" : "bad";
    const seen = new Set();
    for (const c of hub.clients) {
      seen.add(c.pid);
      let li = this.items.get(c.pid);
      if (!li) {
        li = el("li");
        li.innerHTML = `<a><span class="portrait mini"><img alt=""></span><span class="rail-who"><span class="rail-nick"></span><span class="rail-hp"><span></span></span></span><span class="dot" aria-hidden="true"></span></a>`;
        $("a", li).href = `#/p/${c.pid}`;
        this.items.set(c.pid, li);
        this.list.append(li);
      }
      const s = c.snapshot;
      const id = (s && s.ident) || {};
      setFace($("img", li), id);
      $(".rail-nick", li).textContent = id.nick || `PID ${c.pid}`;
      $(".rail-hp span", li).style.width = s && s.hp.max ? `${Math.min(100, (100 * s.hp.cur) / s.hp.max)}%` : "0";
      $(".dot", li).dataset.state = c.radar;
      $("a", li).setAttribute("aria-current", String(detailView.pid === c.pid && current === "detail"));
    }
    for (const [pid, li] of this.items) if (!seen.has(pid)) { li.remove(); this.items.delete(pid); }
    if (!hub.clients.length && !this.list.querySelector(".rail-empty")) this.list.append(el("li", "rail-empty", "Żaden klient nie działa"));
    if (hub.clients.length) this.list.querySelector(".rail-empty")?.remove();
  },
};

/* ======================= Postacie ======================= */
const listView = {
  root: $("#view-list"),
  roster: $("#roster"),
  summary: $("#summary"),
  tpl: $("#tpl-plaque"),
  plaques: new Map(),   // pid -> element

  makePlaque(pid) {
    const p = this.tpl.content.firstElementChild.cloneNode(true);
    p.dataset.pid = pid;
    $(".open", p).href = `#/p/${pid}`;
    wireActions(p, () => pid, () => hub.refresh().then(() => this.render()));
    wireAutologin(p, pid, () => hub.refresh().then(() => this.render()));
    return p;
  },

  renderSwitches(p, pid, options) {
    const box = $(".switches", p);
    const wanted = SWITCHES.filter(([name]) => options && name in options);
    const keys = wanted.map(([n]) => n).join();
    if (box.dataset.keys !== keys) {
      box.replaceChildren();
      for (const [name, label] of wanted) {
        const b = el("button", "switch", label);
        b.type = "button";
        b.dataset.name = name;
        b.addEventListener("click", async () => {
          b.disabled = true;
          try {
            const opts = await sendOption(pid, name, b.getAttribute("aria-pressed") !== "true");
            b.setAttribute("aria-pressed", String(opts[name].value));
          } catch (e) { showNotice(`${label}: ${e.message}`); }
          b.disabled = false;
        });
        box.append(b);
      }
      box.dataset.keys = keys;
    }
    for (const b of box.children) {
      if (!b.disabled) b.setAttribute("aria-pressed", String(!!options[b.dataset.name].value));
    }
  },

  renderPlaque(p, c) {
    const s = c.snapshot;
    p.classList.toggle("is-off", c.radar !== "on");
    renderIdentity(p, c, s, $(".open", p));
    this.renderSwitches(p, c.pid, s ? s.options : null);
    renderAutologin(p, c.autologin);
    const foot = $(".foot", p);
    const parts = [`${c.exe}, PID ${c.pid}.`, `Bot ${RADAR_STATE[c.radar] || c.radar}.`];
    if (s && s.status) {
      if (s.status.combat) parts.push(`Walka: ${s.status.combat}.`);
      if (s.follow && s.follow.leader) parts.push(`Idzie za ${s.follow.leader}: ${s.follow.state}.`);
    }
    foot.textContent = parts.join(" ");
    if (!c.window_visible) foot.append(el("span", "warn", " Okno gry jest ukryte."));
  },

  render() {
    const list = hub.clients;
    if (!hub.ok) { this.summary.textContent = "Brak połączenia z hubem. Uruchom panel.bat."; return; }
    const bots = list.filter((c) => c.radar === "on").length;
    this.summary.textContent = list.length
      ? `${plural(list.length, "klient", "klienty", "klientów")}, ${plural(bots, "bot działa", "boty działają", "botów działa")}.`
      : "Żaden klient gry nie działa.";
    if (!list.length) {
      this.plaques.clear();
      if (!$(".empty", this.roster)) {
        this.roster.replaceChildren();
        const d = el("div", "empty");
        d.innerHTML = "<strong>Żaden klient gry nie działa</strong>Uruchom nowego klienta. Po zalogowaniu bot wystartuje sam, bez żadnego okna.";
        this.roster.append(d);
      }
      return;
    }
    $(".empty", this.roster)?.remove();
    const seen = new Set();
    for (const c of list) {
      seen.add(c.pid);
      let p = this.plaques.get(c.pid);
      if (!p) { p = this.makePlaque(c.pid); this.plaques.set(c.pid, p); this.roster.append(p); }
      this.renderPlaque(p, c);
    }
    for (const [pid, p] of this.plaques) {
      if (!seen.has(pid)) { p.remove(); this.plaques.delete(pid); }
    }
  },
};

/* ---- autologowanie na tabliczce klienta ---- */
function wireAutologin(p, pid, afterChange) {
  const form = $(".al-form", p);
  form.addEventListener("input", () => { form.dataset.dirty = "1"; });
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = form.elements;
    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    try {
      await post(`/api/clients/${pid}/autologin`, {
        login: f.login.value, password: f.password.value,
        channel: Number(f.channel.value), slot: Number(f.slot.value),
        enabled: f.enabled.checked, restart: f.restart.checked, radar: f.radar.checked,
      });
      f.password.value = "";
      delete form.dataset.dirty;
      form.dataset.key = "";
      showNotice(f.enabled.checked ? "Zapisano. Bot zaloguje klienta sam." : "Zapisano (autologowanie wyłączone).", "ok");
      await afterChange();
    } catch (e) { showNotice(`Autologowanie: ${e.message}`); }
    btn.disabled = false;
  });
  $(".al-relog", form).addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await post(`/api/clients/${pid}/relog`);
      showNotice("Wylogowuję. Bot zaloguje postać od nowa na ustawiony kanał.", "ok");
    } catch (e) { showNotice(`Przelogowanie: ${e.message}`); }
    btn.disabled = false;
  });
}

function alTone(al) {
  return !al || !al.enabled ? "" : AL_STATE_OK.has(al.status.state) ? "ok" : AL_STATE_BAD.has(al.status.state) ? "bad" : "busy";
}

function renderAutologin(p, al) {
  const state = $(".al-state", p);
  state.textContent = al ? (al.enabled ? al.status.text : "wyłączone") : "nie ustawione";
  state.dataset.tone = alTone(al);
  const form = $(".al-form", p);
  $(".al-relog", form).hidden = !(al && al.enabled);
  const key = JSON.stringify(al ? [al.id, al.login, al.channel, al.slot, al.enabled, al.restart, al.radar, al.has_password] : null);
  if (form.dataset.dirty || form.dataset.key === key) return;   // nie nadpisuj tego, co użytkownik właśnie wpisuje
  form.dataset.key = key;
  const f = form.elements;
  f.login.value = al ? al.login : "";
  f.password.placeholder = al && al.has_password ? "zapisane – zostaw puste" : "";
  f.channel.value = String(al ? al.channel : 1);
  f.slot.value = String(al ? al.slot : 0);
  f.enabled.checked = al ? al.enabled : true;
  f.restart.checked = !!(al && al.restart);
  f.radar.checked = al ? !!al.radar : true;
}

$("#launch").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  try {
    await post("/api/launch");
    showNotice("Uruchamiam nowego klienta. Zaloguj się w oknie gry, bot wystartuje sam po wejściu do świata.", "ok");
  } catch (e) { showNotice(`Nowy klient: ${e.message}`); }
  setTimeout(() => { btn.disabled = false; }, 5000);
});

/* ======================= Konta ======================= */
const accountsView = {
  root: $("#view-accounts"),
  box: $("#account-list"),

  async refresh() {
    let profiles;
    try { profiles = (await getJSON("/api/autologin")).profiles; } catch { return; }
    const running = new Set(hub.clients.map((c) => c.pid));
    const key = JSON.stringify(profiles.map((p) => [p.id, p.login, p.channel, p.slot, p.enabled, p.restart, p.radar, p.pid,
      running.has(p.pid), p.status && p.status.text]));
    if (this.box.dataset.key === key) return;
    this.box.dataset.key = key;
    this.box.replaceChildren();
    if (!profiles.length) {
      const d = el("div", "empty");
      d.innerHTML = "<strong>Brak zapisanych kont</strong>Uruchom klienta, otwórz jego kartę w Postaciach i wypełnij „Autologowanie”.";
      this.box.append(d);
      return;
    }
    for (const p of profiles) {
      const alive = running.has(p.pid);
      const card = el("article", "card account");
      const head = el("div", "account-head");
      head.append(el("h2", null, p.login));
      const st = el("span", "al-state", p.enabled ? (p.status ? p.status.text : "czeka") : "wyłączone");
      st.dataset.tone = alTone(p);
      head.append(st);
      const facts = el("dl", "facts");
      for (const [k, v] of [["Kanał", `CH${p.channel}`], ["Postać", `slot ${p.slot + 1}`],
        ["Klient", alive ? `działa (PID ${p.pid})` : "nie działa"],
        ["Po zamknięciu", p.restart ? "uruchom ponownie" : "nic nie rób"], ["Bot po wejściu", p.radar ? "tak" : "nie"]]) {
        facts.append(el("dt", null, k), el("dd", null, v));
      }
      const acts = el("div", "form-actions");
      if (alive) {
        const open = el("a", "btn btn-quiet", "Otwórz postać");
        open.href = `#/p/${p.pid}`;
        acts.append(open);
      } else {
        const run = el("button", "btn btn-gold", "Uruchom klienta i zaloguj");
        run.type = "button";
        run.addEventListener("click", async () => {
          run.disabled = true;
          try {
            await post(`/api/autologin/${p.id}/launch`);
            showNotice(`Uruchamiam klienta dla konta ${p.login}. Zaloguje się samo.`, "ok");
          } catch (e) { showNotice(`Uruchomienie: ${e.message}`); run.disabled = false; }
        });
        acts.append(run);
      }
      const del = el("button", "btn btn-danger", "Usuń konto");
      del.type = "button";
      del.addEventListener("click", async () => {
        if (!confirm(`Usunąć zapisane konto ${p.login} (z hasłem)?`)) return;
        try { await post(`/api/autologin/${p.id}/remove`); this.box.dataset.key = ""; this.refresh(); }
        catch (e) { showNotice(`Usuń: ${e.message}`); }
      });
      acts.append(del);
      card.append(head, facts, acts);
      this.box.append(card);
    }
  },
};

/* ======================= Ustawienia ======================= */
const settingsView = {
  root: $("#view-settings"),
  form: $("#settings-form"),
  files: $("#files"),
  loaded: false,

  async open() {
    try {
      this.fill(await getJSON("/api/settings"));
    } catch (e) { showNotice(`Ustawienia: ${e.message}`); }
    if (!this.loaded) {
      this.loaded = true;
      try {
        for (const f of (await getJSON("/api/files")).files) this.files.append(this.fileCard(f));
      } catch (e) { showNotice(`Pliki: ${e.message}`); }
    }
  },

  fill(cfg) {
    const f = this.form.elements;
    if (!this.form.dataset.dirty) {
      f.game_path.value = cfg.game_path;
      f.radar_window.checked = cfg.radar_window;
    }
    const note = $("#game-note");
    note.textContent = cfg.game_found ? "Plik istnieje." : "Nie ma takiego pliku – popraw ścieżkę.";
    note.dataset.tone = cfg.game_found ? "ok" : "bad";
    const adm = $("#admin-state");
    adm.textContent = cfg.admin
      ? "Panel działa jako administrator – bot może wstrzyknąć DLL i czytać pamięć gry."
      : "Panel działa BEZ uprawnień administratora – bot nie wstrzyknie DLL. Zamknij go i uruchom panel.bat.";
    adm.dataset.tone = cfg.admin ? "ok" : "bad";
  },

  fileCard(f) {
    const card = el("form", "card file");
    card.innerHTML = `<h2></h2><p class="hint"></p><textarea spellcheck="false" rows="12"></textarea>
      <div class="form-actions"><span class="saved"></span><button class="btn btn-gold" type="submit">Zapisz plik</button></div>`;
    $("h2", card).textContent = f.name;
    $(".hint", card).textContent = `${f.about}. Bot czyta ten plik na bieżąco – zmiany działają po zapisaniu.`;
    const ta = $("textarea", card);
    const saved = $(".saved", card);
    getJSON(`/api/files/${encodeURIComponent(f.name)}`).then((d) => { ta.value = d.text; }).catch((e) => { saved.textContent = e.message; });
    ta.addEventListener("input", () => { saved.textContent = "Niezapisane zmiany"; });
    card.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await post(`/api/files/${encodeURIComponent(f.name)}`, { text: ta.value });
        saved.textContent = "Zapisano";
      } catch (e) { showNotice(`${f.name}: ${e.message}`); }
    });
    return card;
  },
};

settingsView.form.addEventListener("input", () => { settingsView.form.dataset.dirty = "1"; });
settingsView.form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = settingsView.form.elements;
  try {
    const cfg = await post("/api/settings", { game_path: f.game_path.value, radar_window: f.radar_window.checked });
    delete settingsView.form.dataset.dirty;
    settingsView.fill(cfg);
    showNotice("Zapisano ustawienia.", "ok");
  } catch (e) { showNotice(`Ustawienia: ${e.message}`); }
});

/* ======================= Dziennik ======================= */
const logsView = {
  root: $("#view-logs"),
  list: $("#log-list"),
  text: $("#log-text"),
  follow: $("#log-follow"),
  name: null,
  last: 0,

  async refresh(force) {
    if (!force && Date.now() - this.last < LOG_POLL_MS) return;
    this.last = Date.now();
    let logs;
    try { logs = (await getJSON("/api/logs")).logs; } catch { return; }
    const key = logs.map((l) => `${l.name}:${l.size}`).join();
    if (this.list.dataset.key !== key) {
      this.list.dataset.key = key;
      this.list.replaceChildren();
      if (!logs.length) this.list.append(el("li", "rail-empty", "Brak logów"));
      for (const l of logs) {
        const li = el("li");
        const b = el("button", "log-item");
        b.type = "button";
        b.append(el("span", "log-name", l.name), el("span", "log-meta",
          `${new Date(l.mtime * 1000).toLocaleString("pl-PL")} · ${(l.size / 1024).toFixed(1)} KB`));
        b.setAttribute("aria-current", String(l.name === this.name));
        b.addEventListener("click", () => { this.name = l.name; this.list.dataset.key = ""; this.refresh(true); });
        li.append(b);
        this.list.append(li);
      }
      if (!this.name && logs.length) { this.name = logs[0].name; this.list.dataset.key = ""; return this.refresh(true); }
    }
    if (this.name && (force || this.follow.checked)) {
      try {
        const d = await getJSON(`/api/logs/${encodeURIComponent(this.name)}`);
        const atEnd = this.text.scrollTop + this.text.clientHeight >= this.text.scrollHeight - 30;
        const t = (d.truncated ? "… (początek pominięty)\n" : "") + (d.text || "(pusty)");
        if (this.text.textContent !== t) {
          this.text.textContent = t;
          if (atEnd || force) this.text.scrollTop = this.text.scrollHeight;
        }
      } catch (e) { this.text.textContent = e.message; }
    }
  },
};

/* ======================= Postać ======================= */
const detailView = {
  root: $("#view-detail"),
  canvas: $("#radar-canvas"),
  tabs: $("#view-detail .tabs"),
  panel: $("#view-detail .tabpanel"),
  status: $("#radar-status"),
  pid: null,
  snap: null,
  client: { radar: "off", window_visible: true },
  tab: "Mikstura",
  viewWorld: 12000,          // ile jednostek świata mieści szerokość radaru (1 px mapy = 100 jedn.)
  showNames: true,
  maps: new Map(),           // nazwa mapy -> HTMLImageElement
  controls: new Map(),       // nazwa opcji -> {el, set(value)}
  tabsKey: "",
  buffsKey: "",

  open(pid) {
    if (this.pid !== pid) {
      this.pid = pid;
      this.snap = null;
      this.tabsKey = this.buffsKey = "";
      this.tabs.replaceChildren();
      this.panel.replaceChildren();
      this.controls.clear();
    }
  },

  syncClient() {
    const pid = this.pid;
    this.client = hub.clients.find((c) => c.pid === pid) || { pid, radar: hub.ok ? "gone" : "off", window_visible: false };
    // kandydaci na lidera: inne postacie z działającym botem
    this.others = hub.clients
      .filter((c) => c.pid !== pid && c.snapshot && c.snapshot.ident.nick)
      .map((c) => ({ nick: c.snapshot.ident.nick, map: c.snapshot.map }));
  },

  async refreshSnap() {
    if (this.pid == null || this.inFlight) return;   // wolny radar: nie piętrz zapytań co 250 ms
    const pid = this.pid;
    this.syncClient();
    let s = null;
    this.inFlight = true;
    try {
      if (this.client.radar === "on") {
        try {
          const r = await fetch(`/api/clients/${pid}/snapshot`, { cache: "no-store" });
          s = r.ok ? await r.json() : this.snap;   // chwilowy błąd: zostaw ostatni obraz
        } catch { s = this.snap; }
      }
    } finally {
      this.inFlight = false;
    }
    if (pid !== this.pid) return;            // w międzyczasie przełączono postać
    this.snap = s;
    this.render();
  },

  render() {
    const s = this.snap, c = this.client;
    const head = $(".detail-head", this.root);
    renderIdentity(head, c, s, $(".nick", head));
    $("#tools").hidden = !s;
    document.title = `${(s && s.ident.nick) || "Postać"} – panel bota`;
    this.renderChips(s);
    if (c.radar === "gone") {
      this.status.textContent = "Ten klient gry już nie działa.";
    } else if (!s) {
      this.status.textContent = c.radar === "starting" ? "Bot się uruchamia – kalibracja trwa kilka sekund…" : "Bot nie działa. Uruchom go przyciskiem powyżej.";
    } else {
      const ents = s.entities || [];
      const n = (k) => ents.filter((e) => e.kind === k).length;
      this.status.textContent = `W zasięgu: ${n("mob")} potworów, ${n("boss") + n("king")} bossów, ${n("metin")} metinów, ${n("player")} graczy.`;
    }
    this.renderTabs(s ? s.options : {}, !!(s && s.follow));
    this.renderControls(s);
    this.draw();
  },

  renderChips(s) {
    const box = $("#state-chips");
    const chips = [];
    if (s) {
      const st = s.status;
      // dll_status radaru: "wstrzyknieta, exec" / "wstrzyknieta, hook" / "wstrzyknieta, BEZ exec" / "brak (...)"
      const dll = st.dll || "";
      chips.push(/exec$/.test(dll) && !/BEZ/.test(dll) ? ["DLL działa", "ok"]
        : /hook$/.test(dll) ? ["DLL działa (hook)", "ok"]
          : dll.startsWith("brak") ? [`Brak DLL ${dll.slice(4)}`, "bad"] : ["DLL bez wykonywania", "warn"]);
      if (st.pot_mode) chips.push([`Mikstury ${st.pot_mode}`, st.pot_mode === "bez fokusu" ? "ok" : "warn"]);
      if (st.mount) chips.push([st.mount.on ? `Na koniu (${st.mount.model})` : "Pieszo", ""]);
    }
    const key = JSON.stringify(chips);
    if (box.dataset.key === key) return;
    box.dataset.key = key;
    box.replaceChildren(...chips.map(([t, tone]) => { const li = el("li", null, t); li.dataset.tone = tone; return li; }));
  },

  /* ---------- opcje ---------- */
  renderTabs(options, hasFollow) {
    const avail = TABS.filter(([, names]) => names.some((n) => n in options)).map(([t]) => t);
    if (hasFollow) avail.splice(avail.indexOf("Speedhack") < 0 ? avail.length : avail.indexOf("Speedhack"), 0, FOLLOW_TAB);
    const key = avail.join();
    if (key === this.tabsKey) return;
    this.tabsKey = key;
    this.tabs.replaceChildren();
    if (!avail.includes(this.tab) && avail.length) this.tab = avail[0];
    for (const title of avail) {
      const b = el("button", "tab", title);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.addEventListener("click", () => { this.tab = title; this.tabsKey = ""; this.render(); });
      this.tabs.append(b);
    }
    for (const b of this.tabs.children) b.setAttribute("aria-selected", String(b.textContent === this.tab));
    this.controls.clear();
    this.panel.replaceChildren();
    this.buffsKey = "";
  },

  renderControls(s) {
    if (!s) return;
    const def = TABS.find(([t]) => t === this.tab);
    if (def) {
      let state = $(".tab-state", this.panel);
      if (!state) { state = el("p", "tab-state"); this.panel.prepend(state); }
      // starszy radar nie ma części pól stanu - liczniki od zera zamiast "undefined"
      const st = { pot_count: 0, loot_count: 0, metins: 0, area_atk_count: 0, cape_count: 0, buff_count: 0, waypoints: 0,
        pot_mode: "?", pot_vnums: [], ...s.status };
      try { state.textContent = def[2]({ ...s, status: st }); } catch { state.textContent = ""; }
    }
    const names = (def ? def[1] : []).filter((n) => n in s.options);
    for (const name of names) {
      let ctl = this.controls.get(name);
      if (!ctl) {
        ctl = this.makeControl(name, s.options[name]);
        this.controls.set(name, ctl);
        this.panel.append(ctl.el);
      }
      ctl.set(s.options[name].value);
    }
    if (this.tab === "Buffy") this.renderBuffs(s.buffs || []);
    if (this.tab === FOLLOW_TAB) this.renderFollow(s);
  },

  makeControl(name, spec) {
    const pid = () => this.pid;
    const meta = OPT[name] || name;
    const label = Array.isArray(meta) ? meta[0] : meta;
    const row = el("div", `opt opt-${spec.type}`);

    if (spec.type === "bool") {
      const b = el("button", "toggle");
      b.type = "button";
      b.innerHTML = `<span class="knob" aria-hidden="true"></span><span></span>`;
      b.lastChild.textContent = label;
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const opts = await sendOption(pid(), name, b.getAttribute("aria-pressed") !== "true");
          b.setAttribute("aria-pressed", String(opts[name].value));
        } catch (e) { showNotice(`${label}: ${e.message}`); }
        b.disabled = false;
      });
      row.append(b);
      return { el: row, set: (v) => { if (!b.disabled) b.setAttribute("aria-pressed", String(!!v)); } };
    }

    if (spec.type === "list" || (spec.type === "str" && spec.choices)) {
      const single = spec.type === "str";
      const box = el("div", single ? "segmented" : "chips");
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", label);
      for (const ch of spec.choices) {
        const b = el("button", single ? "seg" : "switch", (single ? CHOICE_PL : POT_SIZE_PL)[ch] || ch);
        b.type = "button";
        b.dataset.choice = ch;
        b.addEventListener("click", async () => {
          let next = ch;
          if (!single) {
            const cur = [...box.children].filter((x) => x.getAttribute("aria-pressed") === "true").map((x) => x.dataset.choice);
            next = b.getAttribute("aria-pressed") === "true" ? cur.filter((x) => x !== ch) : [...cur, ch];
          }
          b.disabled = true;
          try { await sendOption(pid(), name, next); } catch (e) { showNotice(`${label}: ${e.message}`); }
          b.disabled = false;
        });
        box.append(b);
      }
      row.append(el("span", "opt-label", label), box);
      return {
        el: row,
        set: (v) => {
          for (const b of box.children) {
            if (!b.disabled) b.setAttribute("aria-pressed", String(single ? v === b.dataset.choice : v.includes(b.dataset.choice)));
          }
        },
      };
    }

    // liczba: suwak; wysyłamy na 'change' (puszczenie), w trakcie przeciągania tylko podgląd wartości
    const unit = Array.isArray(meta) ? meta[1] : "";
    const step = (Array.isArray(meta) && meta[2]) || 1;
    const id = `opt-${name}`;
    const lab = el("label", "opt-label", label);
    lab.htmlFor = id;
    const out = el("output");
    out.htmlFor = id;
    const input = el("input");
    input.type = "range";
    input.id = id;
    input.min = spec.min;
    input.max = spec.max;
    input.step = step;
    const fmt = (v) => `${Number(v).toFixed(step < 1 ? (step < 0.1 ? 2 : 1) : 0)}${unit ? ` ${unit}` : ""}`;
    let dragging = false;
    input.addEventListener("input", () => { dragging = true; out.textContent = fmt(input.value); });
    input.addEventListener("change", async () => {
      try {
        const opts = await sendOption(pid(), name, Number(input.value));
        input.value = opts[name].value;
        // handler radaru może przesunąć zależne suwaki (np. "Lecz do" nie niżej niż "Pij poniżej")
        for (const [n, ctl] of this.controls) if (n !== name && opts[n]) ctl.set(opts[n].value);
      } catch (e) { showNotice(`${label}: ${e.message}`); }
      dragging = false;
      out.textContent = fmt(input.value);
    });
    const head = el("div", "opt-head");
    head.append(lab, out);
    row.append(head, input);
    return { el: row, set: (v) => { if (!dragging && document.activeElement !== input) { input.value = v; out.textContent = fmt(v); } } };
  },

  renderFollow(s) {
    const f = s.follow;
    let box = $(".follow", this.panel);
    if (!box) {
      box = el("div", "opt follow");
      box.innerHTML = `
        <p class="tab-state follow-state"></p>
        <label class="opt-label" for="follow-leader">Idź za postacią</label>
        <select id="follow-leader"></select>
        <div class="opt-head"><label class="opt-label" for="follow-dist">Trzymaj się w odległości</label><output for="follow-dist"></output></div>
        <input type="range" id="follow-dist" min="200" max="3000" step="100">
        <div class="form-actions"><button type="button" class="btn btn-gold" id="follow-go"></button></div>
        <p class="hint">Postać idzie trasą z omijaniem przeszkód i staje w podanej odległości. Na czas podążania
        farma i auto-chodzenie wyłączają się. Lider musi mieć działającego bota i być na tej samej mapie.</p>`;
      this.panel.append(box);
      const dist = $("#follow-dist", box);
      dist.value = f.distance;
      $("output", box).textContent = f.distance;
      dist.addEventListener("input", () => { $("output", box).textContent = dist.value; });
      dist.addEventListener("change", async () => {
        const cur = this.snap && this.snap.follow;
        if (cur && cur.leader) {           // w trakcie podążania zmiana dystansu działa od razu
          try { await sendOption(this.pid, "follow", { leader: cur.leader, distance: Number(dist.value) }); }
          catch (e) { showNotice(`Podążanie: ${e.message}`); }
        }
      });
      $("#follow-go", box).addEventListener("click", async (ev) => {
        const btn = ev.currentTarget;
        const cur = this.snap && this.snap.follow;
        const leader = cur && cur.leader ? null : $("#follow-leader", box).value;
        if (leader === "") { showNotice("Podążanie: wybierz postać, za którą iść."); return; }
        btn.disabled = true;
        try {
          await sendOption(this.pid, "follow", { leader, distance: Number(dist.value) });
          await this.refreshSnap();
        } catch (e) { showNotice(`Podążanie: ${e.message}`); }
        btn.disabled = false;
      });
    }
    const sel = $("#follow-leader", box);
    const others = [...(this.others || [])];
    const key = others.map((o) => `${o.nick}@${o.map}`).join() + `|${f.leader || ""}`;
    if (sel.dataset.key !== key && document.activeElement !== sel) {
      const keep = f.leader || sel.value;
      sel.replaceChildren();
      if (f.leader && !others.some((o) => o.nick === f.leader)) others.unshift({ nick: f.leader, map: null });
      if (!others.length) sel.append(new Option("Brak innych postaci z działającym botem", ""));
      for (const o of others) {
        const where = o.map == null ? "bot nie działa" : o.map === s.map ? "ta sama mapa" : mapLabel(o.map);
        sel.append(new Option(`${o.nick} (${where})`, o.nick));
      }
      if (keep) sel.value = keep;
      sel.dataset.key = key;
    }
    sel.disabled = !!f.leader;
    $(".follow-state", box).textContent = f.leader ? `Idę za ${f.leader}: ${f.state}.` : "Podążanie wyłączone.";
    const go = $("#follow-go", box);
    go.textContent = f.leader ? "Przestań podążać" : "Idź za wybraną postacią";
    go.className = `btn${f.leader ? " btn-quiet" : " btn-gold"}`;
  },

  renderBuffs(buffs) {
    let box = $(".buffs", this.panel);
    const key = buffs.map((b) => b.vnum).join();
    if (!box) {
      box = el("div", "opt buffs");
      this.panel.append(box);
    }
    if (key !== this.buffsKey) {
      this.buffsKey = key;
      box.replaceChildren(el("span", "opt-label", "Odnawiane umiejętności"));
      const chips = el("div", "chips");
      box.append(chips);
      if (!buffs.length) chips.append(el("p", "hint", "Postać nie ma wyuczonych umiejętności-buffów (albo bot jeszcze ich nie odczytał)."));
      for (const bf of buffs) {
        const b = el("button", "switch", `${bf.name} · slot ${bf.slot}`);
        b.type = "button";
        b.dataset.vnum = bf.vnum;
        b.addEventListener("click", async () => {
          b.disabled = true;
          try { await sendOption(this.pid, "buff", { vnum: bf.vnum, on: b.getAttribute("aria-pressed") !== "true" }); }
          catch (e) { showNotice(`${bf.name}: ${e.message}`); }
          b.disabled = false;
        });
        chips.append(b);
      }
    }
    for (const b of box.querySelectorAll(".switch")) {
      const bf = buffs.find((x) => String(x.vnum) === b.dataset.vnum);
      if (bf && !b.disabled) b.setAttribute("aria-pressed", String(bf.on));
    }
  },

  /* ---------- radar ---------- */
  mapImage(name) {
    if (!name) return null;
    let img = this.maps.get(name);
    if (!img) {
      img = new Image();
      img.src = `/maps/${encodeURIComponent(name)}.png?t=${Date.now()}`;
      img.onload = () => this.draw();
      this.maps.set(name, img);
    }
    return img.complete && img.naturalWidth ? img : null;
  },

  draw() {
    const cv = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const W = Math.round(cv.clientWidth * dpr), H = Math.round(cv.clientHeight * dpr);
    if (!W || !H) return;
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    const g = cv.getContext("2d");
    g.fillStyle = "#120d0b";
    g.fillRect(0, 0, W, H);
    const s = this.snap;
    if (!s || !s.pos) return;

    const hx = s.pos.x, hy = s.pos.y;
    const k = W / this.viewWorld;                 // px canvasu na jednostkę świata
    const toX = (wx) => W / 2 + (wx - hx) * k;
    const toY = (wy) => H / 2 + (wy - hy) * k;
    const r = (n) => n * dpr;

    // tło: wycinek minimapy (1 px obrazu = 100 jednostek świata, współrzędne lokalne mapy)
    const img = this.mapImage(s.map);
    if (img) {
      const vw = this.viewWorld, vh = this.viewWorld * (H / W);
      g.imageSmoothingEnabled = true;
      g.drawImage(img, (hx - vw / 2) / 100, (hy - vh / 2) / 100, vw / 100, vh / 100, 0, 0, W, H);
      g.fillStyle = "rgba(18, 13, 11, 0.25)";      // lekko przyciemnione, żeby znaczniki były czytelne
      g.fillRect(0, 0, W, H);
    }

    const ov = s.overlay || {};
    const ring = (x, y, rad, stroke, fill, dash) => {
      g.beginPath();
      g.arc(x, y, rad, 0, Math.PI * 2);
      if (fill) { g.fillStyle = fill; g.fill(); }
      g.setLineDash(dash || []);
      g.strokeStyle = stroke;
      g.lineWidth = r(1.5);
      g.stroke();
      g.setLineDash([]);
    };
    if (ov.farm) ring(toX(ov.farm.x), toY(ov.farm.y), ov.farm.r * k, "rgba(226, 85, 63, 0.7)", "rgba(226, 85, 63, 0.10)");
    if (ov.area_r) ring(W / 2, H / 2, ov.area_r * k, "rgba(255, 94, 168, 0.75)", null, [r(6), r(4)]);
    if (ov.path && ov.path.length) {
      g.setLineDash([r(5), r(4)]);
      g.strokeStyle = "rgba(95, 163, 138, 0.9)";
      g.lineWidth = r(1.5);
      g.beginPath();
      ov.path.forEach(([x, y], i) => (i ? g.lineTo(toX(x), toY(y)) : g.moveTo(toX(x), toY(y))));
      g.closePath();
      g.stroke();
      g.setLineDash([]);
      ov.path.forEach(([x, y], i) => {
        g.beginPath();
        g.arc(toX(x), toY(y), r(i === ov.walk_idx ? 5 : 3.5), 0, Math.PI * 2);
        g.fillStyle = i === ov.walk_idx ? "#9fe0c6" : "#5fa38a";
        g.fill();
      });
    }
    if (ov.route && ov.route.length) {
      g.strokeStyle = "#f0a03a";
      g.lineWidth = r(2);
      g.beginPath();
      g.moveTo(W / 2, H / 2);
      for (const [x, y] of ov.route) g.lineTo(toX(x), toY(y));
      g.stroke();
    }
    // miejsca, w których postać utknęła (przeszkody spoza siatki kolizji)
    g.strokeStyle = "#b98be6";
    g.lineWidth = r(2);
    for (const [x, y] of ov.learned || []) {
      const X = toX(x), Y = toY(y), h = r(4);
      g.beginPath();
      g.moveTo(X - h, Y - h); g.lineTo(X + h, Y + h);
      g.moveTo(X + h, Y - h); g.lineTo(X - h, Y + h);
      g.stroke();
    }
    // dropy na ziemi: małe złote romby
    g.fillStyle = "#e9d5a0";
    for (const [x, y] of ov.ground || []) {
      const X = toX(x), Y = toY(y), h = r(3.5);
      g.beginPath();
      g.moveTo(X, Y - h); g.lineTo(X + h, Y); g.lineTo(X, Y + h); g.lineTo(X - h, Y);
      g.fill();
    }

    const labels = [];
    for (const e of s.entities || []) {
      const x = toX(e.x), y = toY(e.y);
      if (x < -10 || y < -10 || x > W + 10 || y > H + 10) continue;
      g.fillStyle = KIND_COLOR[e.kind] || KIND_COLOR.mob;
      g.strokeStyle = "rgba(0,0,0,0.75)";
      g.lineWidth = r(1);
      if (e.kind === "metin") {
        const h = r(6);
        g.fillRect(x - h, y - h, 2 * h, 2 * h);
        g.strokeRect(x - h, y - h, 2 * h, 2 * h);
      } else {
        g.beginPath();
        g.arc(x, y, r(e.kind === "boss" || e.kind === "king" ? 6 : 4), 0, Math.PI * 2);
        g.fill();
        g.stroke();
      }
      if (this.showNames && e.name && e.kind !== "npc") labels.push([x, y, e.name, KIND_COLOR[e.kind]]);
    }
    g.font = `${r(11)}px "IBM Plex Sans", sans-serif`;
    g.textAlign = "center";
    for (const [x, y, name, col] of labels) {
      g.fillStyle = "rgba(0,0,0,0.8)";
      g.fillText(name, x + r(1), y - r(9));
      g.fillStyle = col;
      g.fillText(name, x, y - r(10));
    }

    if (ov.target) {
      g.strokeStyle = ov.target.melee ? "#e2553f" : "#f2cf4a";
      g.lineWidth = r(2);
      g.beginPath();
      g.arc(toX(ov.target.x), toY(ov.target.y), r(11), 0, Math.PI * 2);
      g.stroke();
    }

    // postać: złoty pierścień na środku (jak ramka portretu)
    g.beginPath();
    g.arc(W / 2, H / 2, r(7), 0, Math.PI * 2);
    g.fillStyle = "#1d1714";
    g.fill();
    g.lineWidth = r(3);
    g.strokeStyle = "#c9a45c";
    g.stroke();
  },

  zoom(f) {
    this.viewWorld = Math.min(60000, Math.max(3000, this.viewWorld * f));
    this.draw();
  },
};

/* ---------- narzędzia radaru (dawne klawisze J/K/R/M w oknie tkinter) ---------- */
$("#tools .menu-list").addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-action]");
  if (!b) return;
  const action = b.dataset.action;
  $("#tools").open = false;
  showNotice(`${ACTION_PL[action]}: trwa…`, "busy");
  try {
    await sendOption(detailView.pid, "action", action);
    if (action === "rebuild_map" && detailView.snap) detailView.maps.delete(detailView.snap.map);
    showNotice(`${ACTION_PL[action]}: gotowe.`, "ok");
  } catch (e) { showNotice(`${ACTION_PL[action]}: ${e.message}`); }
});
document.addEventListener("click", (ev) => { if (!ev.target.closest("#tools")) $("#tools").open = false; });

/* ---------- trasa: punkty klikane na mapie ---------- */
const routeEdit = $("#route-edit");

async function routeCommand(name, value) {
  try {
    await sendOption(detailView.pid, name, value);
    await detailView.refreshSnap();
  } catch (e) { showNotice(`Trasa: ${e.message}`); }
}

routeEdit.addEventListener("click", () => {
  const on = routeEdit.getAttribute("aria-pressed") !== "true";
  routeEdit.setAttribute("aria-pressed", String(on));
  routeEdit.textContent = on ? "Kończę dodawanie punktów" : "Dodawaj punkty trasy";
  detailView.canvas.classList.toggle("is-editing", on);
});
$("#route-undo").addEventListener("click", () => routeCommand("waypoint_undo"));
$("#route-clear").addEventListener("click", () => {
  const n = ((detailView.snap && detailView.snap.overlay && detailView.snap.overlay.path) || []).length;
  if (n && confirm(`Usunąć całą trasę (${plural(n, "punkt", "punkty", "punktów")}) z tej mapy?`)) routeCommand("waypoint_clear");
});
detailView.canvas.addEventListener("click", (ev) => {
  const s = detailView.snap;
  if (routeEdit.getAttribute("aria-pressed") !== "true" || !s || !s.pos) return;
  const rect = detailView.canvas.getBoundingClientRect();
  const k = rect.width / detailView.viewWorld;          // px CSS na jednostkę świata (jak w draw)
  routeCommand("waypoint_add", {
    x: Math.round(s.pos.x + (ev.clientX - rect.left - rect.width / 2) / k),
    y: Math.round(s.pos.y + (ev.clientY - rect.top - rect.height / 2) / k),
  });
});

wireActions($(".detail-head", detailView.root), () => detailView.pid, async () => { await hub.refresh(); await detailView.refreshSnap(); });
$("#zoom-in").addEventListener("click", () => detailView.zoom(1 / 1.4));
$("#zoom-out").addEventListener("click", () => detailView.zoom(1.4));
$("#show-names").addEventListener("change", (ev) => { detailView.showNames = ev.target.checked; detailView.draw(); });
detailView.canvas.addEventListener("wheel", (ev) => { ev.preventDefault(); detailView.zoom(ev.deltaY > 0 ? 1.15 : 1 / 1.15); }, { passive: false });
new ResizeObserver(() => detailView.draw()).observe(detailView.canvas);

/* ======================= routing + odpytywanie ======================= */
const VIEWS = { list: listView, detail: detailView, accounts: accountsView, settings: settingsView, logs: logsView };
const ROUTES = { "#/konta": "accounts", "#/ustawienia": "settings", "#/dziennik": "logs" };
const TITLES = { list: "Postacie", accounts: "Konta i logowanie", settings: "Ustawienia", logs: "Dziennik" };
let current = null;

function route() {
  const m = location.hash.match(/^#\/p\/(\d+)$/);
  current = m ? "detail" : ROUTES[location.hash] || "list";
  for (const [name, v] of Object.entries(VIEWS)) v.root.hidden = name !== current;
  for (const a of document.querySelectorAll(".nav a")) {
    a.setAttribute("aria-current", String(a.dataset.view === current));
  }
  if (m) {
    detailView.open(Number(m[1]));
    detailView.refreshSnap();
  } else {
    detailView.pid = null;
    document.title = `${TITLES[current]} – panel bota`;
  }
  tick();
  if (current === "settings") settingsView.open();
  if (current === "logs") logsView.refresh(true);
  rail.render();
  $(".stage").scrollTo(0, 0);
}

async function tick() {
  await hub.refresh();
  if (current === "list") listView.render();
  else if (current === "accounts") accountsView.refresh();
  else if (current === "logs") logsView.refresh();
}

window.addEventListener("hashchange", route);
route();
setInterval(tick, LIST_POLL_MS);
setInterval(() => { if (current === "detail") detailView.refreshSnap(); }, DETAIL_POLL_MS);
