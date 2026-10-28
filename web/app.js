// Panel bota: lista postaci z hubu (recon/hub.py). Odpytuje /api/clients co sekunde i aktualizuje
// tabliczki w miejscu (bez przebudowy DOM - fokus klawiatury i kliknięcia nie znikają przy odświeżeniu).
"use strict";

const POLL_MS = 1000;
const CLASS_PL = { warrior: "Wojownik", assassin: "Ninja", sura: "Sura", shaman: "Szaman", wolfman: "Lykan" };
const CLASS_PL_W = { warrior: "Wojowniczka", assassin: "Ninja", sura: "Sura", shaman: "Szamanka", wolfman: "Lykanka" };
// szybkie przełączniki na tabliczce: opcja API radaru -> etykieta (pokazywane, gdy radar ją ma)
const SWITCHES = [
  ["autopot", "Mikstura"],
  ["autoloot", "Loot"],
  ["farm", "Farma"],
  ["autowalk", "Chodzenie"],
  ["autobuff", "Buffy"],
];
const RADAR_STATE = {
  on: "działa",
  off: "nie działa",
  starting: "uruchamia się (kalibracja trwa kilka sekund)",
  busy: "nie odpowiada",
  error: "zwrócił błąd",
};

const roster = document.getElementById("roster");
const summary = document.getElementById("summary");
const notice = document.getElementById("notice");
const tpl = document.getElementById("tpl-plaque");
const plaques = new Map();   // pid -> element
let noticeTimer = null;

function showNotice(text) {
  notice.textContent = text;
  notice.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice.hidden = true; }, 6000);
}

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.ok === false) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function plural(n, one, few, many) {
  if (n === 1) return `${n} ${one}`;
  const d = n % 10, t = n % 100;
  return `${n} ${d >= 2 && d <= 4 && (t < 12 || t > 14) ? few : many}`;
}

function mapLabel(name) {
  return name ? name.replace(/^metin2_map_/, "mapa ") : "mapa nieznana";
}

function setBar(el, cur, max) {
  const ok = Number.isFinite(cur) && Number.isFinite(max) && max > 0;
  el.querySelector(".fill").style.width = ok ? `${Math.max(0, Math.min(100, (100 * cur) / max))}%` : "0";
  el.querySelector(".num").textContent = ok ? `${cur} / ${max}` : "–";
}

function makePlaque(pid) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  el.dataset.pid = pid;
  el.querySelector(".act-window").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await post(`/api/clients/${pid}/window`, { visible: btn.dataset.visible !== "true" });
      await refresh();
    } catch (e) { showNotice(`Okno gry: ${e.message}`); }
    btn.disabled = false;
  });
  el.querySelector(".act-radar").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const starting = btn.dataset.state !== "on";
    btn.disabled = true;
    try {
      await post(`/api/clients/${pid}/radar/${starting ? "start" : "stop"}`);
      await refresh();
    } catch (e) {
      showNotice(`${starting ? "Uruchomienie" : "Zamknięcie"} radaru: ${e.message}`);
      btn.disabled = false;
    }
  });
  return el;
}

function renderSwitches(el, pid, options) {
  const box = el.querySelector(".switches");
  const wanted = SWITCHES.filter(([name]) => options && name in options);
  if (box.dataset.keys !== wanted.map(([n]) => n).join()) {
    box.replaceChildren();
    for (const [name, label] of wanted) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "switch";
      b.dataset.name = name;
      b.textContent = label;
      b.addEventListener("click", async () => {
        const next = b.getAttribute("aria-pressed") !== "true";
        b.disabled = true;
        try {
          const res = await post(`/api/clients/${pid}/command`, { name, value: next });
          b.setAttribute("aria-pressed", String(res.options[name].value));
        } catch (e) { showNotice(`${label}: ${e.message}`); }
        b.disabled = false;
      });
      box.append(b);
    }
    box.dataset.keys = wanted.map(([n]) => n).join();
  }
  for (const b of box.children) {
    if (!b.disabled) b.setAttribute("aria-pressed", String(!!options[b.dataset.name].value));
  }
}

function renderPlaque(el, c) {
  const s = c.snapshot;
  const id = (s && s.ident) || {};
  el.classList.toggle("is-off", c.radar !== "on");

  const portrait = el.querySelector(".portrait");
  const img = portrait.querySelector("img");
  const face = id.face ? `/faces/${id.face}` : "";
  if (img.getAttribute("src") !== face) face ? img.setAttribute("src", face) : img.removeAttribute("src");
  portrait.classList.toggle("is-empty", !face);

  const cls = id.cls ? (id.sex === "w" ? CLASS_PL_W : CLASS_PL)[id.cls] || id.cls : null;
  el.querySelector(".nick").textContent = id.nick || (c.radar === "on" ? "Postać poza światem" : "Klient bez radaru");
  el.querySelector(".meta").textContent = s
    ? [cls, id.level ? `poziom ${id.level}` : null, mapLabel(s.map)].filter(Boolean).join(", ")
    : "Uruchom radar, gdy postać będzie już w świecie gry.";

  el.querySelector(".bars").hidden = !s;
  if (s) {
    setBar(el.querySelector(".bar-hp"), s.hp.cur, s.hp.max);
    setBar(el.querySelector(".bar-sp"), s.sp.cur, s.sp.max);
  }
  renderSwitches(el, c.pid, s ? s.options : null);

  const win = el.querySelector(".act-window");
  win.dataset.visible = String(c.window_visible);
  win.textContent = c.window_visible ? "Ukryj okno gry" : "Pokaż okno gry";

  const radar = el.querySelector(".act-radar");
  radar.dataset.state = c.radar;
  radar.textContent = c.radar === "on" ? "Zamknij radar" : c.radar === "starting" ? "Radar startuje…" : "Uruchom radar";
  radar.disabled = c.radar === "starting";
  radar.className = `btn act-radar${c.radar === "on" ? "" : " btn-gold"}`;

  const foot = el.querySelector(".foot");
  const parts = [`Klient ${c.exe}, PID ${c.pid}.`, `Radar ${RADAR_STATE[c.radar] || c.radar}.`];
  if (s && s.status) {
    if (s.status.dll) parts.push(`DLL: ${s.status.dll}.`);
    if (s.status.combat) parts.push(`Walka: ${s.status.combat}.`);
  }
  foot.textContent = parts.join(" ");
  if (!c.window_visible) {
    const w = document.createElement("span");
    w.className = "warn";
    w.textContent = " Okno gry jest ukryte.";
    foot.append(w);
  }
}

function renderEmpty() {
  roster.replaceChildren();
  plaques.clear();
  const d = document.createElement("div");
  d.className = "empty";
  d.innerHTML = "<strong>Żaden klient gry nie działa</strong>Uruchom nowego klienta, zaloguj się, a radar wystartuje sam.";
  roster.append(d);
}

async function refresh() {
  let data;
  try {
    const r = await fetch("/api/clients", { cache: "no-store" });
    data = await r.json();
  } catch {
    summary.textContent = "Brak połączenia z hubem. Czy python recon/hub.py nadal działa?";
    return;
  }
  const list = data.clients;
  const radars = list.filter((c) => c.radar === "on").length;
  summary.textContent = list.length
    ? `${plural(list.length, "klient", "klienty", "klientów")}, ${plural(radars, "radar działa", "radary działają", "radarów działa")}.`
    : "Brak klientów gry.";
  if (!list.length) { renderEmpty(); return; }
  roster.querySelector(".empty")?.remove();

  const seen = new Set();
  for (const c of list) {
    seen.add(c.pid);
    let el = plaques.get(c.pid);
    if (!el) { el = makePlaque(c.pid); plaques.set(c.pid, el); roster.append(el); }
    renderPlaque(el, c);
  }
  for (const [pid, el] of plaques) {
    if (!seen.has(pid)) { el.remove(); plaques.delete(pid); }
  }
}

document.getElementById("launch").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  try {
    await post("/api/launch");
    showNotice("Uruchamiam nowego klienta. Zaloguj się w nowym oknie, radar wystartuje sam po wejściu do świata.");
  } catch (e) { showNotice(`Nowy klient: ${e.message}`); }
  setTimeout(() => { btn.disabled = false; }, 5000);
});

refresh();
setInterval(refresh, POLL_MS);
