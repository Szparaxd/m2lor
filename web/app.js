// Panel bota (recon/hub.py). Dwa widoki:
//   #/        lista postaci (odpytuje /api/clients co sekundę),
//   #/p/<pid> jedna postać: radar na canvasie + wszystkie opcje (odpytuje /api/clients/<pid>/snapshot ~4x/s).
// Elementy są aktualizowane w miejscu (bez przebudowy DOM), żeby odświeżanie nie gubiło fokusu i kliknięć.
"use strict";

const LIST_POLL_MS = 1000;
const DETAIL_POLL_MS = 250;
const CLASS_PL = { warrior: "Wojownik", assassin: "Ninja", sura: "Sura", shaman: "Szaman", wolfman: "Lykan" };
const CLASS_PL_W = { warrior: "Wojowniczka", assassin: "Ninja", sura: "Sura", shaman: "Szamanka", wolfman: "Lykanka" };
// szybkie przełączniki na tabliczce listy (pokazywane, gdy radar ma daną opcję)
const SWITCHES = [["autopot", "Mikstura"], ["autoloot", "Loot"], ["farm", "Farma"], ["autowalk", "Chodzenie"], ["autobuff", "Buffy"]];
const RADAR_STATE = {
  on: "działa", off: "nie działa", starting: "uruchamia się (kalibracja trwa kilka sekund)",
  busy: "nie odpowiada", error: "zwrócił błąd",
};
// zakładki widoku postaci: opcje API radaru w kolejności wyświetlania
const TABS = [
  ["Mikstura", ["autopot", "pot_low", "pot_high", "pot_cd", "pot_sizes"]],
  ["Loot", ["autoloot", "loot_interval", "loot_radius"]],
  ["Farma", ["farm", "farm_radius", "farm_metin", "farm_boss", "prep_dismount", "prep_buffs", "prep_remount"]],
  ["Chodzenie", ["autowalk", "walk_fight"]],
  ["Buffy", ["autobuff"]],
  ["Speedhack", ["speed", "move_mult", "atk_mult"]],
];
const OPT = {
  autopot: "Auto-mikstura",
  pot_low: ["Pij poniżej", "% HP"],
  pot_high: ["Lecz do", "% HP"],
  pot_cd: ["Odstęp między miksturami", "s", 0.05],
  pot_sizes: "Rozmiary mikstur",
  autoloot: "Auto-loot",
  loot_interval: ["Zbieraj co", "s", 0.1],
  loot_radius: ["Zasięg zbierania", "", 100],
  farm: "Auto-farma w okręgu wokół postaci",
  farm_radius: ["Promień farmy", "", 100],
  farm_metin: "Najpierw bij metiny",
  farm_boss: "Najpierw bij bossy",
  prep_dismount: "Przed bossem zsiądź z konia",
  prep_buffs: "Przed bossem użyj Aury i Szału",
  prep_remount: "Po walce z bossem wsiądź na konia",
  autowalk: "Auto-chodzenie po trasie",
  walk_fight: "W drodze bij metiny i bossy",
  autobuff: "Auto-buff",
  speed: "Speedhack (test, czy serwer pilnuje prędkości)",
  move_mult: ["Mnożnik ruchu", "×", 0.1],
  atk_mult: ["Mnożnik ataku", "×", 0.1],
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
const notice = $("#notice");
let noticeTimer = null;

function showNotice(text) {
  notice.textContent = text;
  notice.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice.hidden = true; }, 6000);
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

function setBar(el, cur, max) {
  const ok = Number.isFinite(cur) && Number.isFinite(max) && max > 0;
  $(".fill", el).style.width = ok ? `${Math.max(0, Math.min(100, (100 * cur) / max))}%` : "0";
  $(".num", el).textContent = ok ? `${cur} / ${max}` : "–";
}

// wspólne dla tabliczki i nagłówka widoku postaci: portret, nick, opis, paski, przyciski
function renderIdentity(el, c, s, nickTarget) {
  const id = (s && s.ident) || {};
  const portrait = $(".portrait", el);
  const img = $("img", portrait);
  const face = id.face ? `/faces/${id.face}` : "";
  if (img.getAttribute("src") !== face) face ? img.setAttribute("src", face) : img.removeAttribute("src");
  portrait.classList.toggle("is-empty", !face);

  nickTarget.textContent = id.nick || (c.radar === "on" ? "Postać poza światem" : "Klient bez radaru");
  $(".meta", el).textContent = s
    ? [className(id), id.level ? `poziom ${id.level}` : null, mapLabel(s.map)].filter(Boolean).join(", ")
    : "Uruchom radar, gdy postać będzie już w świecie gry.";
  $(".bars", el).hidden = !s;
  if (s) {
    setBar($(".bar-hp", el), s.hp.cur, s.hp.max);
    setBar($(".bar-sp", el), s.sp.cur, s.sp.max);
  }
  const win = $(".act-window", el);
  win.dataset.visible = String(c.window_visible);
  win.textContent = c.window_visible ? "Ukryj okno gry" : "Pokaż okno gry";
  const radar = $(".act-radar", el);
  radar.dataset.state = c.radar;
  radar.textContent = c.radar === "on" ? "Zamknij radar" : c.radar === "starting" ? "Radar startuje…" : "Uruchom radar";
  radar.disabled = c.radar === "starting";
  radar.className = `btn act-radar${c.radar === "on" ? "" : " btn-gold"}`;
}

function wireActions(el, getPid, afterChange) {
  $(".act-window", el).addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await post(`/api/clients/${getPid()}/window`, { visible: btn.dataset.visible !== "true" });
      await afterChange();
    } catch (e) { showNotice(`Okno gry: ${e.message}`); }
    btn.disabled = false;
  });
  $(".act-radar", el).addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const starting = btn.dataset.state !== "on";
    btn.disabled = true;
    try {
      await post(`/api/clients/${getPid()}/radar/${starting ? "start" : "stop"}`);
      await afterChange();
    } catch (e) {
      showNotice(`${starting ? "Uruchomienie" : "Zamknięcie"} radaru: ${e.message}`);
      btn.disabled = false;
    }
  });
}

async function sendOption(pid, name, value) {
  const res = await post(`/api/clients/${pid}/command`, { name, value });
  return res.options;
}

/* ======================= widok 1: lista ======================= */
const listView = {
  root: $("#view-list"),
  roster: $("#roster"),
  summary: $("#summary"),
  tpl: $("#tpl-plaque"),
  plaques: new Map(),   // pid -> element

  makePlaque(pid) {
    const el = this.tpl.content.firstElementChild.cloneNode(true);
    el.dataset.pid = pid;
    $(".open", el).href = `#/p/${pid}`;
    wireActions(el, () => pid, () => this.refresh());
    wireAutologin(el, pid, () => this.refresh());
    return el;
  },

  renderSwitches(el, pid, options) {
    const box = $(".switches", el);
    const wanted = SWITCHES.filter(([name]) => options && name in options);
    const keys = wanted.map(([n]) => n).join();
    if (box.dataset.keys !== keys) {
      box.replaceChildren();
      for (const [name, label] of wanted) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "switch";
        b.dataset.name = name;
        b.textContent = label;
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

  renderPlaque(el, c) {
    const s = c.snapshot;
    el.classList.toggle("is-off", c.radar !== "on");
    renderIdentity(el, c, s, $(".open", el));
    this.renderSwitches(el, c.pid, s ? s.options : null);
    renderAutologin(el, c.autologin);
    const foot = $(".foot", el);
    const parts = [`Klient ${c.exe}, PID ${c.pid}.`, `Radar ${RADAR_STATE[c.radar] || c.radar}.`];
    if (s && s.status) {
      if (s.status.dll) parts.push(`DLL: ${s.status.dll}.`);
      if (s.status.combat) parts.push(`Walka: ${s.status.combat}.`);
      if (s.follow && s.follow.leader) parts.push(`Idzie za ${s.follow.leader}: ${s.follow.state}.`);
    }
    foot.textContent = parts.join(" ");
    if (!c.window_visible) {
      const w = document.createElement("span");
      w.className = "warn";
      w.textContent = " Okno gry jest ukryte.";
      foot.append(w);
    }
  },

  renderEmpty() {
    this.roster.replaceChildren();
    this.plaques.clear();
    const d = document.createElement("div");
    d.className = "empty";
    d.innerHTML = "<strong>Żaden klient gry nie działa</strong>Uruchom nowego klienta, zaloguj się, a radar wystartuje sam.";
    this.roster.append(d);
  },

  async refresh() {
    let data;
    try {
      data = await (await fetch("/api/clients", { cache: "no-store" })).json();
    } catch {
      this.summary.textContent = "Brak połączenia z hubem. Czy python recon/hub.py nadal działa?";
      return;
    }
    const list = data.clients;
    const radars = list.filter((c) => c.radar === "on").length;
    this.summary.textContent = list.length
      ? `${plural(list.length, "klient", "klienty", "klientów")}, ${plural(radars, "radar działa", "radary działają", "radarów działa")}.`
      : "Brak klientów gry.";
    if (!list.length) { this.renderEmpty(); return; }
    $(".empty", this.roster)?.remove();
    const seen = new Set();
    for (const c of list) {
      seen.add(c.pid);
      let el = this.plaques.get(c.pid);
      if (!el) { el = this.makePlaque(c.pid); this.plaques.set(c.pid, el); this.roster.append(el); }
      this.renderPlaque(el, c);
    }
    for (const [pid, el] of this.plaques) {
      if (!seen.has(pid)) { el.remove(); this.plaques.delete(pid); }
    }
    this.renderAccounts(seen);
  },

  // profile autologowania, których klient nie działa (zamknięty, restart wyłączony albo trwa uruchamianie)
  async renderAccounts(running) {
    let profiles;
    try {
      profiles = (await (await fetch("/api/autologin", { cache: "no-store" })).json()).profiles;
    } catch { return; }
    const box = $("#accounts");
    const list = $(".account-list", box);
    const orphans = profiles.filter((p) => !running.has(p.pid));
    box.hidden = !orphans.length;
    const key = JSON.stringify(orphans.map((p) => [p.id, p.login, p.channel, p.slot, p.status && p.status.text]));
    if (list.dataset.key === key) return;
    list.dataset.key = key;
    list.replaceChildren();
    for (const p of orphans) {
      const li = document.createElement("li");
      const who = document.createElement("span");
      who.className = "account-who";
      who.textContent = `${p.login} · CH${p.channel} · postać ${p.slot + 1}`;
      const st = document.createElement("span");
      st.className = "account-state";
      st.textContent = p.status ? p.status.text : "";
      const run = document.createElement("button");
      run.type = "button";
      run.className = "btn btn-gold";
      run.textContent = "Uruchom klienta";
      run.addEventListener("click", async () => {
        run.disabled = true;
        try {
          await post(`/api/autologin/${p.id}/launch`);
          showNotice(`Uruchamiam klienta dla konta ${p.login}. Zaloguje się samo.`);
        } catch (e) { showNotice(`Uruchomienie: ${e.message}`); run.disabled = false; }
      });
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn btn-quiet";
      del.textContent = "Usuń";
      del.addEventListener("click", async () => {
        try { await post(`/api/autologin/${p.id}/remove`); list.dataset.key = ""; } catch (e) { showNotice(`Usuń: ${e.message}`); }
      });
      li.append(who, st, run, del);
      list.append(li);
    }
  },
};

/* ---- autologowanie na tabliczce klienta ---- */
function wireAutologin(el, pid, afterChange) {
  const form = $(".al-form", el);
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
      showNotice(f.enabled.checked ? "Zapisane. Bot zaloguje klienta sam." : "Zapisane (autologowanie wyłączone).");
      await afterChange();
    } catch (e) { showNotice(`Autologowanie: ${e.message}`); }
    btn.disabled = false;
  });
  $(".al-relog", form).addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await post(`/api/clients/${pid}/relog`);
      showNotice("Wylogowuję – bot zaloguje postać od nowa na ustawiony kanał.");
    } catch (e) { showNotice(`Przelogowanie: ${e.message}`); }
    btn.disabled = false;
  });
}

function renderAutologin(el, al) {
  const state = $(".al-state", el);
  state.textContent = al ? (al.enabled ? al.status.text : "wyłączone") : "nie ustawione";
  state.dataset.tone = !al || !al.enabled ? "" : AL_STATE_OK.has(al.status.state) ? "ok"
    : AL_STATE_BAD.has(al.status.state) ? "bad" : "busy";
  const form = $(".al-form", el);
  $(".al-relog", form).hidden = !(al && al.enabled);
  const key = JSON.stringify(al ? [al.id, al.login, al.channel, al.slot, al.enabled, al.restart, al.radar, al.has_password] : null);
  if (form.dataset.dirty || form.dataset.key === key) return;   // nie nadpisuj tego, co user właśnie wpisuje
  form.dataset.key = key;
  const f = form.elements;
  f.login.value = al ? al.login : "";
  f.password.placeholder = al && al.has_password ? "zapisane – zostaw puste" : "";
  f.channel.value = String(al ? al.channel : 1);
  f.slot.value = String(al ? al.slot : 0);
  f.enabled.checked = al ? al.enabled : true;
  f.restart.checked = !!(al && al.restart);
  f.radar.checked = !!(al && al.radar);
}

$("#launch").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  try {
    await post("/api/launch");
    showNotice("Uruchamiam nowego klienta. Zaloguj się w nowym oknie, radar wystartuje sam po wejściu do świata.");
  } catch (e) { showNotice(`Nowy klient: ${e.message}`); }
  setTimeout(() => { btn.disabled = false; }, 5000);
});

/* ======================= widok 2: postać ======================= */
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

  async refresh() {
    if (this.pid == null) return;
    const pid = this.pid;
    try {
      const list = await (await fetch("/api/clients", { cache: "no-store" })).json();
      this.client = list.clients.find((c) => c.pid === pid) || { pid, radar: "gone", window_visible: false };
      // kandydaci na lidera: inne postacie z działającym radarem
      this.others = list.clients
        .filter((c) => c.pid !== pid && c.snapshot && c.snapshot.ident.nick)
        .map((c) => ({ nick: c.snapshot.ident.nick, map: c.snapshot.map }));
    } catch { /* lista co sekundę wystarczy; błąd pokaże refreshSnap */ }
  },

  async refreshSnap() {
    if (this.pid == null || this.inFlight) return;   // wolny radar: nie piętrz zapytań co 250 ms
    const pid = this.pid;
    let s = null;
    this.inFlight = true;
    try {
      if (this.client.radar === "on") {
        try {
          const r = await fetch(`/api/clients/${pid}/snapshot`, { cache: "no-store" });
          if (r.ok) s = await r.json();
          else s = this.snap;                  // chwilowy błąd: zostaw ostatni obraz
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
    document.title = `${(s && s.ident.nick) || "Postać"} – panel bota`;
    if (c.radar === "gone") {
      this.status.textContent = "Ten klient gry już nie działa.";
    } else if (!s) {
      this.status.textContent = c.radar === "starting" ? "Radar się uruchamia…" : "Radar nie działa. Uruchom go przyciskiem powyżej.";
    } else {
      const st = s.status;
      const bits = [`${(s.entities || []).length} obiektów w zasięgu`];
      if (st.waypoints) bits.push(`trasa: ${st.waypoints} punktów`);
      if (st.combat) bits.push(`walka: ${st.combat}`);
      this.status.textContent = bits.join(". ") + ".";
    }
    this.renderTabs(s ? s.options : {}, !!(s && s.follow));
    this.renderControls(s);
    this.draw();
  },

  /* ---------- opcje ---------- */
  renderTabs(options, hasFollow) {
    const avail = TABS.filter(([, names]) => names.some((n) => n in options));
    if (hasFollow) avail.push(["Podążanie", []]);
    const key = avail.map(([t]) => t).join();
    if (key === this.tabsKey) return;
    this.tabsKey = key;
    this.tabs.replaceChildren();
    if (!avail.find(([t]) => t === this.tab) && avail.length) this.tab = avail[0][0];
    for (const [title] of avail) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "tab";
      b.setAttribute("role", "tab");
      b.textContent = title;
      b.addEventListener("click", () => { this.tab = title; this.tabsKey = ""; this.controls.clear(); this.panel.replaceChildren(); this.buffsKey = ""; this.render(); });
      this.tabs.append(b);
    }
    for (const b of this.tabs.children) b.setAttribute("aria-selected", String(b.textContent === this.tab));
    this.controls.clear();
    this.panel.replaceChildren();
    this.buffsKey = "";
  },

  renderControls(s) {
    if (!s) return;
    const names = (TABS.find(([t]) => t === this.tab) || [null, []])[1].filter((n) => n in s.options);
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
    if (this.tab === "Podążanie") this.renderFollow(s);
  },

  makeControl(name, spec) {
    const pid = () => this.pid;
    const meta = OPT[name] || name;
    const label = Array.isArray(meta) ? meta[0] : meta;
    const row = document.createElement("div");
    row.className = `opt opt-${spec.type}`;

    if (spec.type === "bool") {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "toggle";
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

    if (spec.type === "list") {
      const lab = document.createElement("span");
      lab.className = "opt-label";
      lab.textContent = label;
      const box = document.createElement("div");
      box.className = "chips";
      for (const ch of spec.choices) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "switch";
        b.dataset.choice = ch;
        b.textContent = POT_SIZE_PL[ch] || ch;
        b.addEventListener("click", async () => {
          const cur = [...box.children].filter((x) => x.getAttribute("aria-pressed") === "true").map((x) => x.dataset.choice);
          const next = b.getAttribute("aria-pressed") === "true" ? cur.filter((x) => x !== ch) : [...cur, ch];
          b.disabled = true;
          try { await sendOption(pid(), name, next); } catch (e) { showNotice(`${label}: ${e.message}`); }
          b.disabled = false;
        });
        box.append(b);
      }
      row.append(lab, box);
      return {
        el: row,
        set: (v) => { for (const b of box.children) if (!b.disabled) b.setAttribute("aria-pressed", String(v.includes(b.dataset.choice))); },
      };
    }

    // liczba: suwak; wysyłamy na 'change' (puszczenie), w trakcie przeciągania tylko podgląd wartości
    const unit = Array.isArray(meta) ? meta[1] : "";
    const step = (Array.isArray(meta) && meta[2]) || 1;
    const id = `opt-${name}`;
    const lab = document.createElement("label");
    lab.className = "opt-label";
    lab.htmlFor = id;
    lab.textContent = label;
    const out = document.createElement("output");
    out.htmlFor = id;
    const input = document.createElement("input");
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
    const head = document.createElement("div");
    head.className = "opt-head";
    head.append(lab, out);
    row.append(head, input);
    return { el: row, set: (v) => { if (!dragging && document.activeElement !== input) { input.value = v; out.textContent = fmt(v); } } };
  },

  renderFollow(s) {
    const f = s.follow;
    let box = $(".follow", this.panel);
    if (!box) {
      box = document.createElement("div");
      box.className = "opt follow";
      box.innerHTML = `
        <p class="follow-state"></p>
        <label class="opt-label" for="follow-leader">Idź za postacią</label>
        <select id="follow-leader"></select>
        <div class="opt-head"><label class="opt-label" for="follow-dist">Trzymaj się w odległości</label><output for="follow-dist"></output></div>
        <input type="range" id="follow-dist" min="200" max="3000" step="100">
        <div class="follow-actions"><button type="button" class="btn btn-gold" id="follow-go"></button></div>
        <p class="hint">Postać idzie trasą z omijaniem przeszkód i staje w podanej odległości. Na czas podążania
        farma i auto-chodzenie wyłączają się. Lider musi mieć działający radar i być na tej samej mapie.</p>`;
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
    const others = this.others || [];
    const key = others.map((o) => `${o.nick}@${o.map}`).join() + `|${f.leader || ""}`;
    if (sel.dataset.key !== key && document.activeElement !== sel) {
      const keep = f.leader || sel.value;
      sel.replaceChildren();
      const names = others.map((o) => o.nick);
      if (f.leader && !names.includes(f.leader)) others.unshift({ nick: f.leader, map: null });
      if (!others.length) sel.append(new Option("Brak innych postaci z działającym radarem", ""));
      for (const o of others) {
        const where = o.map == null ? "radar nie działa" : o.map === s.map ? "ta sama mapa" : mapLabel(o.map);
        sel.append(new Option(`${o.nick} (${where})`, o.nick));
      }
      if (keep) sel.value = keep;
      sel.dataset.key = key;
    }
    sel.disabled = !!f.leader;
    $(".follow-state", box).textContent = f.leader ? `Idę za ${f.leader}: ${f.state}.` : "Podążanie wyłączone.";
    const go = $("#follow-go", box);
    go.textContent = f.leader ? "Przestań podążać" : "Idź za wybraną postacią";
    go.className = `btn${f.leader ? "" : " btn-gold"}`;
  },

  renderBuffs(buffs) {
    let box = $(".buffs", this.panel);
    const key = buffs.map((b) => b.vnum).join();
    if (!box) {
      box = document.createElement("div");
      box.className = "opt buffs";
      this.panel.append(box);
    }
    if (key !== this.buffsKey) {
      this.buffsKey = key;
      box.replaceChildren();
      if (!buffs.length) {
        box.innerHTML = '<p class="hint">Postać nie ma wyuczonych umiejętności-buffów (albo radar jeszcze ich nie odczytał).</p>';
      }
      for (const bf of buffs) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "switch";
        b.dataset.vnum = bf.vnum;
        b.textContent = `${bf.name} (slot ${bf.slot})`;
        b.addEventListener("click", async () => {
          b.disabled = true;
          try { await sendOption(this.pid, "buff", { vnum: bf.vnum, on: b.getAttribute("aria-pressed") !== "true" }); }
          catch (e) { showNotice(`${bf.name}: ${e.message}`); }
          b.disabled = false;
        });
        box.append(b);
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
      img.src = `/maps/${encodeURIComponent(name)}.png`;
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
    const r = (n) => n * dpr;
    if (ov.farm) {
      g.beginPath();
      g.arc(toX(ov.farm.x), toY(ov.farm.y), ov.farm.r * k, 0, Math.PI * 2);
      g.fillStyle = "rgba(226, 85, 63, 0.10)";
      g.fill();
      g.strokeStyle = "rgba(226, 85, 63, 0.7)";
      g.lineWidth = r(1.5);
      g.stroke();
    }
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

/* ---------- trasa: punkty klikane na mapie (jak Ctrl+klik w radarze tkinter) ---------- */
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

wireActions($(".detail-head", detailView.root), () => detailView.pid, async () => { await detailView.refresh(); detailView.render(); });
$("#zoom-in").addEventListener("click", () => detailView.zoom(1 / 1.4));
$("#zoom-out").addEventListener("click", () => detailView.zoom(1.4));
detailView.canvas.addEventListener("wheel", (ev) => { ev.preventDefault(); detailView.zoom(ev.deltaY > 0 ? 1.15 : 1 / 1.15); }, { passive: false });
new ResizeObserver(() => detailView.draw()).observe(detailView.canvas);

/* ======================= routing + odpytywanie ======================= */
let current = null;

function route() {
  const m = location.hash.match(/^#\/p\/(\d+)$/);
  current = m ? "detail" : "list";
  listView.root.hidden = current !== "list";
  detailView.root.hidden = current !== "detail";
  if (m) {
    detailView.open(Number(m[1]));
    detailView.refresh().then(() => detailView.refreshSnap());
  } else {
    detailView.pid = null;
    document.title = "Postacie – panel bota";
    listView.refresh();
  }
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);
route();
setInterval(() => (current === "list" ? listView.refresh() : detailView.refresh()), LIST_POLL_MS);
setInterval(() => { if (current === "detail") detailView.refreshSnap(); }, DETAIL_POLL_MS);
