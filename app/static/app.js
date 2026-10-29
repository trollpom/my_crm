"use strict";

/* ============================ helpers ============================ */
const $ = (sel, root = document) => root.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), 2200);
}

const PRIO_RU = { high: "Высокий", normal: "Обычный", low: "Низкий" };
const STITLE = { todo: "К выполнению", doing: "В работе", done: "Готово", cancelled: "Отменено" };

/* ============================ уведомления ============================ */
function applyBadges() {
  const t = (state.unread && state.unread.tasks) || {};
  const p = (state.unread && state.unread.projects) || {};
  document.querySelectorAll("[data-ubt]").forEach((el) => {
    const n = t[el.dataset.ubt] || 0;
    el.textContent = n > 99 ? "99+" : n;
    el.classList.toggle("hidden", !n);
  });
  document.querySelectorAll("[data-ubp]").forEach((el) => {
    const n = p[el.dataset.ubp] || 0;
    el.textContent = n > 99 ? "99+" : n;
    el.classList.toggle("hidden", !n);
  });
}

function notify(title, body) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(title, { body }); } catch (e) {}
}

function updateBell(r) {
  const badge = $("#bell-badge");
  if (!badge) return;
  const n = (r && r.count) || 0;
  badge.textContent = n > 99 ? "99+" : n;
  badge.classList.toggle("hidden", !n);
  const fired = (r && r.fired) || [];
  fired.forEach((f) => {
    if (state.remindShown.has(f.id)) return;
    state.remindShown.add(f.id);
    if (state.remindInit) {
      toast(`Напоминание: ${f.title}`);
      notify(`Напоминание: ${f.title}`, f.deadline ? `Дедлайн: ${fmtDateTime(f.deadline)}` : "");
    }
  });
  state.remindInit = true;
  state.lastReminders = r;
  if (state.bellOpen) renderBell();
}

async function pollAlerts() {
  try {
    const [u, r] = await Promise.all([api("/api/activity/unread"), api("/api/reminders")]);
    state.unread = u || { tasks: {}, projects: {} };
    applyBadges();
    updateBell(r);
  } catch (e) {}
}

function renderBell() {
  const panel = $("#bell-panel");
  if (!panel) return;
  const r = state.lastReminders || {};
  const sec = (arr, title, cls) => (arr && arr.length)
    ? `<div class="bp-sec">${title}</div>` + arr.map((x) => `
        <div class="bp-item ${cls || ""}" data-bptask="${x.id}">
          <span class="bp-title">${esc(x.title)}</span>
          <span class="bp-when">${x.deadline ? fmtDateTime(x.deadline) : ""}${x.project_name ? ` · ${esc(x.project_name)}` : ""}</span>
        </div>`).join("")
    : "";
  panel.innerHTML = (sec(r.overdue, "Просроченные", "overdue") +
    sec(r.today, "На сегодня", "today") +
    sec(r.upcoming, "Ближайшие дедлайны (3 дня)") +
    sec(r.fired, "Сработавшие напоминания")) || `<div class="bp-empty">Напоминаний нет</div>`;
  panel.querySelectorAll("[data-bptask]").forEach((el) => {
    el.onclick = async () => {
      closeBell();
      await ensureData();
      const t = state.tasks.find((x) => x.id == el.dataset.bptask);
      if (t) openTaskModal(t);
    };
  });
}

function closeBell() {
  state.bellOpen = false;
  const p = $("#bell-panel");
  if (p) p.classList.add("hidden");
}

function bindBell() {
  const bell = $("#bell");
  if (!bell) return;
  bell.onclick = (e) => {
    e.stopPropagation();
    state.bellOpen = !state.bellOpen;
    const p = $("#bell-panel");
    if (!p) return;
    if (state.bellOpen) { renderBell(); p.classList.remove("hidden"); }
    else p.classList.add("hidden");
  };
  document.addEventListener("click", (e) => {
    if (!e.target.closest || !e.target.closest(".bell-wrap")) closeBell();
  });
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
  pollAlerts();
}

/* SVG-иконки (вместо эмодзи — глифы на низком DPI рендерятся квадратами) */
const ICON = {
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>`,
  pencil: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="m13.5 6.5 4 4"/></svg>`,
  x: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>`,
  folder: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2.2 2.6h6.8a2 2 0 0 1 2 2v8.4a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>`,
  star: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="m12 4.5 2.35 4.85 5.3.7-3.9 3.7 1 5.25L12 16.5l-4.75 2.5 1-5.25-3.9-3.7 5.3-.7z"/></svg>`,
  starFill: `<svg class="star" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="m12 4.5 2.35 4.85 5.3.7-3.9 3.7 1 5.25L12 16.5l-4.75 2.5 1-5.25-3.9-3.7 5.3-.7z"/></svg>`,
  dot: (c) => `<span class="dot ${c || ""}"></span>`,
  bot: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-9.5L6 20.5V16.5H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z"/><circle cx="9" cy="11" r="1" fill="currentColor" stroke="none"/><circle cx="12.5" cy="11" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="11" r="1" fill="currentColor" stroke="none"/></svg>`,
  list: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="4.5" y="4.5" width="15" height="15" rx="2.5"/><path d="M8 9.5h8M8 13h8M8 16.5h5"/></svg>`,
  mail: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="5.5" width="17" height="13" rx="2.5"/><path d="m4.5 8 7.5 5.2L19.5 8"/></svg>`,
  back: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 6 8.5 12l6 6"/></svg>`,
};

const pillPrio = (t) =>
  `<span class="pill p-${t.priority}"><span class="dot"></span>${PRIO_RU[t.priority] || esc(t.priority)}</span>`;

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit" });
}
function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  const hasTime = iso.includes("T") && !(d.getHours() === 9 && d.getMinutes() === 0);
  return hasTime
    ? d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit" });
}
function relDue(iso) {
  if (!iso) return { text: "—", overdue: false };
  const d = new Date(iso), now = new Date();
  const day = 86400000;
  const dd = Math.floor((d.setHours(0,0,0,0) - now.setHours(0,0,0,0)) / day);
  if (dd < 0) return { text: `просрочено на ${-dd} дн.`, overdue: true };
  if (dd === 0) return { text: "сегодня", overdue: false };
  if (dd === 1) return { text: "завтра", overdue: false };
  if (dd <= 7) return { text: `через ${dd} дн.`, overdue: false };
  return { text: fmtDate(iso), overdue: false };
}
/* локальная дата в YYYY-MM-DD (toISOString даёт UTC — для MSK ночью сдвигает день) */
const dayStr = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const todayStr = () => dayStr(new Date());

/* ============================ state ============================ */
const state = {
  tasks: [], projects: [],
  mailTab: "recent", mailQuery: "", mailDays: 0, mailSort: "newest", mailPage: 1,
  aiConv: null, aiMsgs: [],
  calDate: new Date(), calScale: "month", noteId: null,
  unread: { tasks: {}, projects: {} },
  remindShown: new Set(), remindInit: false, bellOpen: false, lastReminders: null,
};

async function ensureData() {
  if (!state.projects.length) state.projects = await api("/api/projects");
  if (!state.tasks.length) state.tasks = await api("/api/tasks");
}

/* ============================ modal ============================ */
function closeModal() {
  $("#modal").classList.add("hidden");
  $("#modal").innerHTML = "";
  document.querySelectorAll(".dt-pop").forEach((p) => p.remove());
}
function openModal(html) {
  /* фоновый клик НЕ закрывает окно: раньше клик мимо модалки
     сбивал все введённые настройки (теперь закрытие только кнопками) */
  const m = $("#modal");
  m.innerHTML = `<div class="box">${html}</div>`;
  m.classList.remove("hidden");
}
/* закрытие попапов даты по клику мимо / Esc (саму модалку Esc не закрывает) */
document.addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest(".dt-pop, .dt-input")) return;
  document.querySelectorAll(".dt-pop").forEach((p) => p.remove());
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  document.querySelectorAll(".dt-pop").forEach((p) => p.remove());
});

/* ------------------------------ выбор даты и времени ------------------------------
   Свой попап вместо нативного datetime-local: в нативном календаре нет кнопки
   подтверждения, а клик мимо него терял значение. Здесь есть «Готово». */
function dtValue(iso) {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(iso);
  if (!m) return iso;
  return `${m[3]}.${m[2]}.${m[1]}${m[4] ? ` ${m[4]}:${m[5]}` : ""}`;
}

function dtField(id, label, iso) {
  return `<div class="field"><label>${label}</label>
    <input id="${id}" class="dt-input" type="text" readonly
      value="${dtValue(iso)}" data-iso="${esc(iso || "")}"
      placeholder="Выберите дату и время…"></div>`;
}

function openDTPop(input) {
  const already = input._pop && document.body.contains(input._pop);
  document.querySelectorAll(".dt-pop").forEach((p) => p.remove());
  if (already) return; /* повторный клик — закрыть */

  const iso = input.dataset.iso || "";
  let sel = /^\d{4}-\d{2}-\d{2}/.test(iso) ? iso.slice(0, 10) : "";
  let time = /^\d{2}:\d{2}$/.test(iso.slice(11, 16)) ? iso.slice(11, 16) : "09:00";
  let view = sel ? new Date(sel + "T00:00:00") : new Date();

  const pop = document.createElement("div");
  pop.className = "dt-pop";
  document.body.appendChild(pop);
  input._pop = pop;

  const render = () => {
    const y = view.getFullYear(), m = view.getMonth();
    const title = view.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
    const shift = (new Date(y, m, 1).getDay() + 6) % 7;
    const days = new Date(y, m + 1, 0).getDate();
    const today = todayStr();
    const p2 = (n) => String(n).padStart(2, "0");
    let cells = "";
    for (let i = 0; i < shift; i++) cells += "<span></span>";
    for (let d = 1; d <= days; d++) {
      const di = `${y}-${p2(m + 1)}-${p2(d)}`;
      cells += `<button type="button" class="dt-day${di === sel ? " sel" : ""}${di === today ? " today" : ""}" data-d="${di}">${d}</button>`;
    }
    const hh = Array.from({ length: 24 }, (_, h) =>
      `<option value="${p2(h)}" ${p2(h) === time.slice(0, 2) ? "selected" : ""}>${p2(h)}</option>`).join("");
    const mm = Array.from({ length: 60 }, (_, v) =>
      `<option value="${p2(v)}" ${p2(v) === time.slice(3, 5) ? "selected" : ""}>${p2(v)}</option>`).join("");
    pop.innerHTML = `
      <div class="dt-head">
        <button type="button" class="dt-nav" data-mv="-1">‹</button>
        <span class="dt-title">${title}</span>
        <button type="button" class="dt-nav" data-mv="1">›</button>
      </div>
      <div class="dt-dow"><span>пн</span><span>вт</span><span>ср</span><span>чт</span><span>пт</span><span>сб</span><span>вс</span></div>
      <div class="dt-grid">${cells}</div>
      <div class="dt-time"><select class="dt-h">${hh}</select><span class="dt-sep">:</span><select class="dt-m">${mm}</select></div>
      <div class="dt-actions">
        <button type="button" class="btn ghost" data-clear>Очистить</button>
        <button type="button" class="btn primary" data-ok>Готово</button>
      </div>`;
    pop.querySelectorAll(".dt-day").forEach((b) => b.onclick = () => { sel = b.dataset.d; render(); });
    pop.querySelectorAll("[data-mv]").forEach((b) => b.onclick = () => { view = new Date(y, m + Number(b.dataset.mv), 1); render(); });
    pop.querySelector(".dt-h").onchange = (e) => { time = e.target.value + ":" + time.slice(3); };
    pop.querySelector(".dt-m").onchange = (e) => { time = time.slice(0, 3) + e.target.value; };
    pop.querySelector("[data-ok]").onclick = () => {
      if (!sel) { toast("Выберите дату"); return; }
      input.dataset.iso = `${sel}T${time}`;
      input.value = dtValue(input.dataset.iso);
      pop.remove(); input._pop = null;
    };
    pop.querySelector("[data-clear]").onclick = () => {
      input.dataset.iso = ""; input.value = "";
      pop.remove(); input._pop = null;
    };
  };
  render();
  const r = input.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8)) + "px";
  const below = r.bottom + 6;
  pop.style.top = (below + pop.offsetHeight > innerHeight - 8 ? Math.max(8, r.top - pop.offsetHeight - 6) : below) + "px";
}

function projectOptions(sel) {
  return `<option value="">Без проекта</option>` + state.projects.map((p) =>
    `<option value="${p.id}" ${String(p.id) === String(sel) ? "selected" : ""}>${esc(p.name)}</option>`).join("");
}

function openTaskModal(task = null) {
  const t = task || {};
  const isEdit = t.id != null; /* клик из календаря передаёт {deadline} без id — это новая задача */
  const dl = t.deadline ? t.deadline.slice(0, 16) : "";
  const sd = t.start_date ? t.start_date.slice(0, 16) : "";
  openModal(`
    <h2>${isEdit ? "Задача #" + t.id : "Новая задача"}</h2>
    <div class="field"><label>Название</label><input id="f-title" type="text" value="${esc(t.title || "")}"></div>
    <div class="field"><label>Описание</label><textarea id="f-desc">${esc(t.description || "")}</textarea></div>
    <div class="field"><label>Проект</label><select id="f-proj">${projectOptions(t.project_id)}</select></div>
    <div class="field"><label>Статус</label><select id="f-status">
      ${Object.entries(STITLE).map(([k, v]) => `<option value="${k}" ${t.status === k ? "selected" : ""}>${v}</option>`).join("")}
    </select></div>
    <div class="field"><label>Приоритет</label><select id="f-prio">
      <option value="high" ${t.priority === "high" ? "selected" : ""}>Высокий</option>
      <option value="normal" ${!t.priority || t.priority === "normal" ? "selected" : ""}>Обычный</option>
      <option value="low" ${t.priority === "low" ? "selected" : ""}>Низкий</option>
    </select></div>
    ${dtField("f-start", "Начало", sd)}
    ${dtField("f-dl", "Дедлайн", dl)}
    ${dtField("f-remind", "Напомнить (дата и время)", t.remind_at ? t.remind_at.slice(0, 16) : "")}
    <div class="field"><label>Прогресс, %</label><input id="f-prog" type="text" value="${t.progress ?? 0}"></div>
    <div class="modal-actions">
      ${isEdit ? `<button class="btn" id="f-act">${ICON.list} Ход действий</button>` : ""}
      ${isEdit ? `<button class="btn" id="f-del">Удалить</button>` : ""}
      <button class="btn" id="f-cancel">Отмена</button>
      <button class="btn primary" id="f-save">Сохранить</button>
    </div>`);

  $("#f-cancel").onclick = closeModal;
  if (isEdit) $("#f-act").onclick = () => openActivityModal("task", t);
  ["f-start", "f-dl", "f-remind"].forEach((id) => {
    const el = $("#" + id);
    el.onclick = () => openDTPop(el);
  });
  if (isEdit) $("#f-del").onclick = async () => {
    if (!confirm("Удалить задачу?")) return;
    await api(`/api/tasks/${t.id}`, { method: "DELETE" });
    state.tasks = []; state.projects = []; closeModal(); toast("Задача удалена"); render();
  };
  $("#f-save").onclick = async () => {
    const body = {
      title: $("#f-title").value.trim(),
      description: $("#f-desc").value,
      project_id: $("#f-proj").value ? Number($("#f-proj").value) : null,
      status: $("#f-status").value,
      priority: $("#f-prio").value,
      start_date: $("#f-start").dataset.iso || null,
      deadline: $("#f-dl").dataset.iso || null,
      remind_at: $("#f-remind").dataset.iso || null,
      progress: Number($("#f-prog").value) || 0,
    };
    if (!body.title) { toast("Введите название"); return; }
    if (isEdit) await api(`/api/tasks/${t.id}`, { method: "PATCH", body: JSON.stringify(body) });
    else await api("/api/tasks", { method: "POST", body: JSON.stringify(body) });
    state.tasks = []; state.projects = []; closeModal(); toast("Сохранено"); render();
  };
  setTimeout(() => $("#f-title").focus(), 30);
}

/* ============================ overview ============================ */
async function viewOverview() {
  const s = await api("/api/stats");
  const tasks = await api("/api/tasks");
  const open = tasks.filter((t) => t.status === "todo" || t.status === "doing");
  const today = todayStr();
  const todayTasks = open.filter((t) => t.deadline && t.deadline.slice(0, 10) === today);
  const overdue = open.filter((t) => t.deadline && t.deadline.slice(0, 10) < today);
  const upcoming = open.filter((t) => t.deadline && t.deadline.slice(0, 10) > today)
    .sort((a, b) => a.deadline.localeCompare(b.deadline)).slice(0, 8);

  const row = (t) => {
    const r = relDue(t.deadline);
    return `<div class="taskrow" data-open="${t.id}">
      <div class="check ${t.status === "done" ? "on" : ""}">${ICON.check}</div>
      <div class="title">${esc(t.title)}<span class="ubadge hidden" data-ubt="${t.id}"></span></div>
      <div class="meta">
        ${t.project_name ? `<span class="pill proj">${esc(t.project_name)}</span>` : ""}
        <span class="pill p-${t.priority}"><span class="dot"></span>${PRIO_RU[t.priority] || esc(t.priority)}</span>
        ${t.deadline ? `<span class="due ${r.overdue ? "overdue" : ""}">${r.text}</span>` : ""}
      </div></div>`;
  };

  return `
    <h1 class="page-title">Обзор</h1>
    <p class="page-sub">${new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</p>
    <div class="cards">
      <div class="card"><div class="num">${open.length}</div><div class="lbl">Активных задач</div></div>
      <div class="card warn"><div class="num">${overdue.length}</div><div class="lbl">Просрочено</div></div>
      <div class="card"><div class="num">${todayTasks.length}</div><div class="lbl">На сегодня</div></div>
      <div class="card good"><div class="num">${s.by_status.done || 0}</div><div class="lbl">Выполнено всего</div></div>
    </div>
    <div class="section-title">Сегодня и просроченные</div>
    ${(todayTasks.length + overdue.length)
      ? `<div class="tasklist">${[...overdue, ...todayTasks].map(row).join("")}</div>`
      : `<div class="empty">На сегодня открытых задач нет</div>`}
    <div class="section-title">Ближайшие дедлайны</div>
    ${upcoming.length ? `<div class="tasklist">${upcoming.map(row).join("")}</div>`
      : `<div class="empty">Нет предстоящих дедлайнов</div>`}
  `;
}

/* ============================ tasks ============================ */
async function viewTasks() {
  await ensureData();
  return `
    <h1 class="page-title">Задачи и списки</h1>
    <p class="page-sub">Все задачи с фильтрами и сортировкой по дедлайну</p>
    <div class="toolbar">
      <select id="flt-status">
        <option value="">Все статусы</option>
        <option value="todo">К выполнению</option>
        <option value="doing">В работе</option>
        <option value="done">Готово</option>
        <option value="cancelled">Отменено</option>
      </select>
      <select id="flt-proj"><option value="">Все проекты</option>
        ${state.projects.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}
      </select>
      <select id="flt-prio">
        <option value="">Любой приоритет</option>
        <option value="high">Высокий</option><option value="normal">Обычный</option><option value="low">Низкий</option>
      </select>
      <button class="btn primary" id="add-task">+ Задача</button>
    </div>
    <div id="tasks-host"></div>`;
}

function bindTasks() {
  const draw = () => {
    const st = $("#flt-status").value, pj = $("#flt-proj").value, pr = $("#flt-prio").value;
    let list = state.tasks.filter((t) =>
      (!st || t.status === st) && (!pj || String(t.project_id) === pj) && (!pr || t.priority === pr));
    list.sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999") || a.id - b.id);
    $("#tasks-host").innerHTML = list.length
      ? `<div class="tasklist">${list.map(taskRow).join("")}</div>`
      : `<div class="empty">Задач нет</div>`;
  };
  ["flt-status", "flt-proj", "flt-prio"].forEach((id) => { $("#" + id).onchange = draw; });
  $("#add-task").onclick = () => openTaskModal();
  draw();
}

function taskRow(t) {
  const r = relDue(t.deadline);
  return `<div class="taskrow ${t.status === "done" ? "done" : ""}" data-id="${t.id}">
    <div class="check ${t.status === "done" ? "on" : ""}" data-toggle="${t.id}">${ICON.check}</div>
    <div class="title" data-edit="${t.id}">${esc(t.title)}<span class="ubadge hidden" data-ubt="${t.id}"></span></div>
    <div class="meta">
      ${t.project_name ? `<span class="pill proj">${esc(t.project_name)}</span>` : ""}
      <span class="pill p-${t.priority}"><span class="dot"></span>${PRIO_RU[t.priority] || esc(t.priority)}</span>
      ${t.deadline ? `<span class="due ${r.overdue ? "overdue" : ""}">${r.text}</span>` : ""}
      <button class="btn ghost small" data-edit="${t.id}">${ICON.pencil}</button>
    </div></div>`;
}

/* ============================ kanban ============================ */
async function viewKanban() {
  await ensureData();
  const cols = [["todo", "К выполнению", "gray"], ["doing", "В работе", "blue"], ["done", "Готово", "green"]];
  const col = ([key, title, dot]) => {
    const items = state.tasks.filter((t) => t.status === key);
    return `<div class="column" data-col="${key}">
      <h3><span class="dot ${dot}"></span>${title} · ${items.length}</h3>
      ${items.map(kcard).join("")}
    </div>`;
  };
  return `
    <h1 class="page-title">Канбан-доска</h1>
    <p class="page-sub">Перетаскивайте карточки между колонками</p>
    <div class="toolbar"><button class="btn primary" id="add-task">+ Задача</button></div>
    <div class="kanban">${cols.map(col).join("")}</div>`;
}

function kcard(t) {
  const r = relDue(t.deadline);
  return `<div class="kcard" draggable="true" data-id="${t.id}">
    <div class="ktitle">${esc(t.title)}<span class="ubadge hidden" data-ubt="${t.id}"></span></div>
    <div class="kmeta">
      ${t.project_name ? `<span class="pill proj">${esc(t.project_name)}</span>` : ""}
      <span class="pill p-${t.priority}"><span class="dot"></span>${PRIO_RU[t.priority] || esc(t.priority)}</span>
      ${t.deadline ? `<span class="due ${r.overdue ? "overdue" : ""}">${r.text}</span>` : ""}
    </div></div>`;
}

function bindKanban() {
  $("#add-task").onclick = () => openTaskModal();
  let dragId = null;
  document.querySelectorAll(".kcard").forEach((c) => {
    c.ondragstart = (e) => { dragId = c.dataset.id; c.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; };
    c.ondragend = () => { c.classList.remove("dragging"); };
    c.onclick = () => { const t = state.tasks.find((x) => x.id == c.dataset.id); openTaskModal(t); };
  });
  document.querySelectorAll(".column").forEach((col) => {
    col.ondragover = (e) => { e.preventDefault(); col.classList.add("drop"); };
    col.ondragleave = () => col.classList.remove("drop");
    col.ondrop = async (e) => {
      e.preventDefault(); col.classList.remove("drop");
      if (!dragId) return;
      const status = col.dataset.col;
      await api(`/api/tasks/${dragId}`, { method: "PATCH", body: JSON.stringify({ status }) });
      state.tasks = []; render(); toast("Статус обновлён");
    };
  });
}

/* ============================ calendar ============================ */
async function viewCalendar() {
  await ensureData();
  return `
    <h1 class="page-title">Календарь</h1>
    <p class="page-sub">Задачи и дедлайны по дням. Клик по дню — новая задача. Масштаб: месяц или год (Ctrl+колёсико).</p>
    <div class="cal-head">
      <button class="btn nav" id="cal-prev" aria-label="Предыдущий период">‹</button>
      <div class="month" id="cal-month"></div>
      <button class="btn nav" id="cal-next" aria-label="Следующий период">›</button>
      <button class="btn" id="cal-today">Сегодня</button>
      <div class="cal-scale">
        <button class="btn" data-scale="month">Месяц</button>
        <button class="btn" data-scale="year">Год</button>
      </div>
    </div>
    <div id="cal-wrap"><div class="calendar" id="cal-grid"></div></div>`;
}

function bindCalendar() {
  const step = (dir) => {
    if (state.calScale === "year") state.calDate.setFullYear(state.calDate.getFullYear() + dir);
    else state.calDate.setMonth(state.calDate.getMonth() + dir);
    drawCalendar();
  };
  $("#cal-prev").onclick = () => step(-1);
  $("#cal-next").onclick = () => step(1);
  $("#cal-today").onclick = () => { state.calDate = new Date(); drawCalendar(); };
  document.querySelectorAll(".cal-scale [data-scale]").forEach((b) => {
    b.onclick = () => { state.calScale = b.dataset.scale; drawCalendar(); };
  });
  const host = $("#cal-wrap");
  if (host) host.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const scale = e.deltaY < 0 ? "month" : "year";
    if (scale !== state.calScale) { state.calScale = scale; drawCalendar(); }
  }, { passive: false });
  drawCalendar();
}

function drawCalendar() {
  document.querySelectorAll(".cal-scale [data-scale]").forEach((b) =>
    b.classList.toggle("on", b.dataset.scale === state.calScale));
  const grid = $("#cal-grid");
  if (state.calScale === "year") {
    grid.className = "";
    drawYearCalendar();
    return;
  }
  grid.className = "calendar";
  const d = state.calDate;
  const y = d.getFullYear(), m = d.getMonth();
  $("#cal-month").textContent = d.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  const first = new Date(y, m, 1);
  let start = new Date(first);
  const shift = (first.getDay() + 6) % 7;
  start.setDate(first.getDate() - shift);

  const byDay = {};
  state.tasks.forEach((t) => {
    const iso = t.deadline || t.start_date;
    if (!iso) return;
    const key = iso.slice(0, 10);
    (byDay[key] = byDay[key] || []).push(t);
  });

  let html = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((x) => `<div class="cal-dow">${x}</div>`).join("");
  for (let i = 0; i < 42; i++) {
    const day = new Date(start); day.setDate(start.getDate() + i);
    const iso = dayStr(day);
    const other = day.getMonth() !== m;
    const isToday = iso === todayStr();
    const evs = (byDay[iso] || []).slice(0, 3).map((t) =>
      `<div class="cal-ev ${t.priority === "high" ? "high" : ""} ${t.status === "done" ? "done" : ""}">${esc(t.title)}</div>`).join("");
    const more = (byDay[iso] || []).length > 3 ? `<div class="cal-ev more">+ ещё ${byDay[iso].length - 3}</div>` : "";
    html += `<div class="cal-cell ${other ? "other" : ""} ${isToday ? "today" : ""}" data-date="${iso}">
      <div class="daynum">${day.getDate()}</div>${evs}${more}</div>`;
  }
  $("#cal-grid").innerHTML = html;
  document.querySelectorAll(".cal-cell").forEach((cell) => {
    cell.onclick = () => {
      const date = cell.dataset.date;
      openTaskModal({ deadline: date + "T09:00" });
    };
  });
}

function drawYearCalendar() {
  const y = state.calDate.getFullYear();
  $("#cal-month").textContent = y + " год";
  const byDay = {};
  state.tasks.forEach((t) => {
    const iso = t.deadline || t.start_date;
    if (!iso) return;
    const key = iso.slice(0, 10);
    (byDay[key] = byDay[key] || []).push(t);
  });
  const dow = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
  const pad = (n) => String(n).padStart(2, "0");
  let html = "";
  for (let mi = 0; mi < 12; mi++) {
    const first = new Date(y, mi, 1);
    const shift = (first.getDay() + 6) % 7;
    const days = new Date(y, mi + 1, 0).getDate();
    let cells = "";
    for (let i = 0; i < shift; i++) cells += `<span class="cal-mini-day empty"></span>`;
    for (let dd = 1; dd <= days; dd++) {
      const iso = `${y}-${pad(mi + 1)}-${pad(dd)}`;
      const evs = byDay[iso] || [];
      const high = evs.some((t) => t.priority === "high" && t.status !== "done");
      const isToday = iso === todayStr();
      const title = evs.length ? evs.map((t) => t.title).join(" · ") : iso;
      cells += `<span class="cal-mini-day ${evs.length ? "has" : ""} ${high ? "high" : ""} ${isToday ? "today" : ""}" data-date="${iso}" title="${esc(title)}">${dd}</span>`;
    }
    html += `<div class="cal-mini">
      <div class="cal-mini-title" data-month="${mi}" title="Открыть месяц">${first.toLocaleDateString("ru-RU", { month: "long" })}</div>
      <div class="cal-mini-dowrow">${dow.map((x) => `<span>${x}</span>`).join("")}</div>
      <div class="cal-mini-grid">${cells}</div>
    </div>`;
  }
  $("#cal-grid").innerHTML = `<div class="cal-year">${html}</div>`;
  document.querySelectorAll("#cal-grid .cal-mini-title").forEach((el) => {
    el.onclick = () => {
      state.calDate = new Date(y, Number(el.dataset.month), 1);
      state.calScale = "month";
      drawCalendar();
    };
  });
  document.querySelectorAll("#cal-grid .cal-mini-day[data-date]").forEach((el) => {
    el.onclick = () => openTaskModal({ deadline: el.dataset.date + "T09:00" });
  });
}

/* ============================ projects ============================ */
async function viewProjects() {
  await ensureData();
  let base = "";
  try { base = (await api("/api/config/folders")).projects_base || ""; } catch (e) {}
  return `
    <h1 class="page-title">Проекты</h1>
    <p class="page-sub">Прогресс и срок по каждому проекту · для каждого проекта создаётся своя папка</p>
    <div class="toolbar">
      <button class="btn primary" id="add-proj">+ Проект</button>
      <input class="input" id="folders-base" value="${esc(base)}" placeholder="Базовая папка проектов…" style="flex:1;min-width:260px">
      <button class="btn" id="folders-save">Сохранить папку</button>
    </div>
    <div class="proj-grid">
      ${state.projects.map(projCard).join("") || `<div class="empty">Проектов нет</div>`}
    </div>`;
}

function bindProjects() {
  $("#folders-save").onclick = async () => {
    const path = $("#folders-base").value.trim();
    if (!path) return toast("Введите путь");
    try {
      const r = await api("/api/config/folders", { method: "POST", body: JSON.stringify({ projects_base: path }) });
      toast("Базовая папка сохранена: " + r.projects_base);
      $("#folders-base").value = r.projects_base;
    } catch (e) { toast("Ошибка: " + e.message); }
  };
  $("#add-proj").onclick = () => openProjectModal();
  document.querySelectorAll(".proj-card").forEach((c) => {
    c.onclick = (e) => {
      if (e.target.closest("[data-pdel]") || e.target.closest("[data-pedit]") ||
          e.target.closest("[data-pact]") || e.target.closest("[data-padd]") ||
          e.target.closest("[data-ptask]")) return;
      const p = state.projects.find((x) => x.id == c.dataset.id);
      filterByProject(p);
    };
    const act = c.querySelector("[data-pact]");
    if (act) act.onclick = (e) => {
      e.stopPropagation();
      openActivityModal("project", state.projects.find((x) => x.id == c.dataset.pact));
    };
    const add = c.querySelector("[data-padd]");
    if (add) add.onclick = (e) => {
      e.stopPropagation();
      openTaskModal({ project_id: Number(add.dataset.padd) });
    };
    c.querySelectorAll("[data-ptask]").forEach((el) => {
      el.onclick = (e) => {
        e.stopPropagation();
        const t = state.tasks.find((x) => x.id === Number(el.dataset.ptask));
        if (t) openTaskModal(t);
      };
    });
    const edit = c.querySelector("[data-pedit]");
    if (edit) edit.onclick = (e) => {
      e.stopPropagation();
      openProjectModal(state.projects.find((x) => x.id == c.dataset.id));
    };
    const del = c.querySelector("[data-pdel]");
    if (del) del.onclick = async (e) => {
      e.stopPropagation();
      await deleteProject(state.projects.find((x) => x.id == c.dataset.id));
    };
  });
}

async function deleteProject(p) {
  if (!p) return;
  const count = state.tasks.filter((t) => t.project_id === p.id).length;
  const msg = count
    ? `В проекте «${p.name}» задач: ${count}. Задачи останутся, но без проекта. Папка проекта НЕ удаляется. Удалить проект?`
    : `Удалить проект «${p.name}»?`;
  if (!confirm(msg)) return;
  await api(`/api/projects/${p.id}`, { method: "DELETE" });
  state.projects = []; state.tasks = [];
  toast("Проект удалён");
  render();
}

function projCard(p) {
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  const tasks = state.tasks.filter((t) => t.project_id === p.id);
  const tlist = tasks.slice(0, 6).map((t) => `
    <div class="ptask ${t.status === "done" ? "done" : ""}" data-ptask="${t.id}">
      <span class="checkmini ${t.status === "done" ? "on" : ""}">${ICON.check}</span>
      <span class="ptask-title">${esc(t.title)}</span>
    </div>`).join("");
  const tmore = tasks.length > 6 ? `<div class="muted small" style="padding-left:23px">…ещё ${tasks.length - 6}</div>` : "";
  return `<div class="proj-card" data-id="${p.id}">
    <h3><span class="swatch" style="background:${p.color}"></span>${esc(p.name)}
      <span class="ubadge hidden" data-ubp="${p.id}"></span>
      <span style="margin-left:auto;display:flex;gap:4px">
        <button class="btn ghost small" data-pact="${p.id}" title="Ход действий">${ICON.list}</button>
        <button class="btn ghost small" data-pedit="${p.id}" title="Изменить">${ICON.pencil}</button>
        <button class="btn ghost small" data-pdel="${p.id}" title="Удалить">${ICON.x}</button>
      </span></h3>
    <div class="pdesc">${esc(p.description || "—")}</div>
    <div class="bar"><i style="width:${pct}%"></i></div>
    <div class="pstats"><span>${p.done}/${p.total} готово</span><span>${pct}%</span></div>
    ${tasks.length ? `<div class="proj-tasks">${tlist}${tmore}</div>` : `<div class="muted small" style="margin-top:6px">Задач нет — создайте кнопкой ниже</div>`}
    <div class="proj-add"><button class="btn ghost small" data-padd="${p.id}">+ Задача в проект</button></div>
    ${p.folder ? `<div class="pfolder" title="${esc(p.folder)}">${ICON.folder}${esc(p.folder)}</div>` : ""}
  </div>`;
}

async function filterByProject(p) {
  const tasks = state.tasks.filter((t) => t.project_id === p.id);
  openModal(`<h2>${esc(p.name)}</h2>
    <div class="muted small" style="margin-bottom:8px">Задач: ${tasks.length} · записей в журнале: ${p.activity_count ?? 0}</div>
    ${tasks.length ? `<div class="tasklist">${tasks.map(taskRow).join("")}</div>` : `<div class="empty">Задач нет</div>`}
    <div class="modal-actions">
      <button class="btn primary" id="fp-add">+ Задача</button>
      <button class="btn" id="fp-act">${ICON.list} Ход действий</button>
      <button class="btn" id="fp-edit">${ICON.pencil} Изменить проект</button>
      <button class="btn" id="fp-close">Закрыть</button></div>`);
  $("#fp-close").onclick = closeModal;
  $("#fp-add").onclick = () => { closeModal(); openTaskModal({ project_id: p.id }); };
  $("#fp-act").onclick = () => openActivityModal("project", p);
  $("#fp-edit").onclick = () => { closeModal(); openProjectModal(p); };
  document.querySelectorAll("#modal [data-edit]").forEach((el) => {
    el.onclick = () => {
      const t = state.tasks.find((x) => x.id === Number(el.dataset.edit));
      if (t) { closeModal(); openTaskModal(t); }
    };
  });
  document.querySelectorAll("#modal [data-toggle]").forEach((el) => {
    el.onclick = async () => {
      const t = state.tasks.find((x) => x.id === Number(el.dataset.toggle));
      if (!t) return;
      await api(`/api/tasks/${t.id}`, { method: "PATCH", body: JSON.stringify({ status: t.status === "done" ? "todo" : "done" }) });
      state.tasks = [];
      closeModal();
      render();
    };
  });
}

function openProjectModal(project = null) {
  const p = project || {};
  openModal(`<h2>${project ? "Проект #" + p.id : "Новый проект"}</h2>
    <div class="field"><label>Название</label><input id="p-name" type="text" value="${esc(p.name || "")}"></div>
    <div class="field"><label>Описание</label><textarea id="p-desc">${esc(p.description || "")}</textarea></div>
    <div class="field"><label>Цвет</label><input id="p-color" type="color" value="${esc(p.color || "#5b8cff")}"></div>
    <div class="field"><label>Папка проекта (пусто = базовая папка/название)</label>
      <input id="p-folder" type="text" value="${esc(p.folder || "")}" placeholder="C:\\...\\projects\\Название"></div>
    <div class="modal-actions">
      ${project ? `<button class="btn" id="p-act">${ICON.list} Ход действий</button>` : ""}
      ${project ? `<button class="btn danger" id="p-del">Удалить</button>` : ""}
      <button class="btn" id="p-cancel">Отмена</button>
      <button class="btn primary" id="p-save">${project ? "Сохранить" : "Создать"}</button></div>`);
  $("#p-cancel").onclick = closeModal;
  if (project) $("#p-act").onclick = () => { closeModal(); openActivityModal("project", p); };
  if (project) $("#p-del").onclick = async () => { closeModal(); await deleteProject(p); };
  $("#p-save").onclick = async () => {
    const name = $("#p-name").value.trim();
    if (!name) { toast("Введите название"); return; }
    const body = { name, description: $("#p-desc").value, color: $("#p-color").value, folder: $("#p-folder").value.trim() || null };
    try {
      if (project) await api(`/api/projects/${p.id}`, { method: "PATCH", body: JSON.stringify(body) });
      else await api("/api/projects", { method: "POST", body: JSON.stringify(body) });
      state.projects = []; state.tasks = [];
      closeModal(); toast(project ? "Проект сохранён" : "Проект создан"); render();
    } catch (e) { toast("Ошибка: " + e.message); }
  };
}

/* ============================ ход действий ============================ */
const ACT_SRC = { user: "Вы", ai: "ИИ", mail: "Письмо", system: "Авто" };
const ACT_KIND_RU = { note: "заметка", mail: "переписка", ai: "разбор ИИ", change: "изменение", create: "начало" };

function actItem(a) {
  const tlink = a.task_id && a.task_title
    ? `<button class="btn ghost small" data-acttask="${a.task_id}" title="Открыть задачу">#${a.task_id}</button>`
    : "";
  return `<div class="act" data-kind="${a.kind || "note"}">
    <div class="act-rail"><span class="act-dot"></span></div>
    <div class="act-body">
      <div class="act-head">
        <span class="act-title">${esc(a.title || "Запись")}</span>
        ${tlink}
        <span class="act-badge ${esc(a.source || "user")}">${ACT_SRC[a.source] || esc(a.source)}</span>
        <span class="act-when">${fmtDateTime(a.created)}</span>
        <span class="act-tools">
          ${a.mail_uid ? `<button class="btn ghost small" data-actmail="${a.mail_uid}" title="Открыть письмо">${ICON.mail}</button>` : ""}
          <button class="btn ghost small" data-actdel="${a.id}" title="Удалить запись">${ICON.x}</button>
        </span>
      </div>
      <div class="act-text">${esc(a.body || "").replace(/\n/g, "<br>") || `<span class="muted">—</span>`}</div>
    </div></div>`;
}

function activityList(items) {
  if (!items.length) return `<div class="empty">Записей пока нет. Привяжите письмо или добавьте заметку.</div>`;
  return `<div class="timeline">${items.map((a) => actItem(a)).join("")}</div>`;
}

async function openActivityModal(kind, obj) {
  const isTask = kind === "task";
  const base = isTask ? `/api/tasks/${obj.id}/activity` : `/api/projects/${obj.id}/activity`;
  let items = await api(base).catch(() => []);
  let wdata = { watches: [] };

  const draw = () => {
    $("#act-list").innerHTML = activityList(items);
    bindItems();
  };
  const reload = async () => { items = await api(base).catch(() => []); draw(); markSeen(); };
  const markSeen = () => {
    /* журнал открыт — записи считаются прочитанными, кружки гаснут */
    api("/api/activity/seen", {
      method: "POST",
      body: JSON.stringify(isTask ? { task_id: obj.id } : { project_id: obj.id }),
    }).then(() => pollAlerts()).catch(() => {});
  };
  const watchLine = () => {
    const ws = wdata.watches || [];
    if (!ws.length) return "";
    const subjects = ws.map((w) => esc(w.thread_subject || w.subject || `#${w.anchor_uid}`)).join(" · ");
    const letters = ws.reduce((s, w) => s + (w.thread_count || 0), 0);
    return `<div class="watch-line">${ICON.mail}<span><b>Ветка под наблюдением:</b> ${subjects} · писем: ${letters} · новые письма этой темы попадают в журнал автоматически (каждые 5 мин)</span><button class="btn ghost" id="act-check-now">Проверить сейчас</button></div>`;
  };
  const bindWatch = () => {
    const b = $("#act-check-now");
    if (b) b.onclick = async () => {
      b.disabled = true;
      b.textContent = "Проверяю…";
      try {
        const r = await api("/api/monitor/run", { method: "POST" });
        if (r.running) toast("Проверка уже идёт…");
        else if (r.added) toast(`Новых записей: ${r.added}`);
        else toast("Новых писем нет");
        await reloadWatches();
        await reload();
      } catch (e) {
        toast("Ошибка: " + e.message);
        await reloadWatches();
      }
    };
  };
  const reloadWatches = async () => {
    wdata = await api(`${base}/watches`).catch(() => ({ watches: [] }));
    const el = $("#act-watch");
    if (el) { el.innerHTML = watchLine(); bindWatch(); }
  };

  openModal(`
    <h2>Ход действий · ${isTask ? `Задача #${obj.id}` : esc(obj.name)}</h2>
    <div class="act-sub muted small">${isTask ? esc(obj.title) : "журнал изменений и событий проекта"}</div>
    <div class="toolbar" style="margin:10px 0">
      <button class="btn" id="act-from-mail">${ICON.bot} Собрать из писем (ИИ)</button>
      <button class="btn ghost" id="act-force" title="Перезаписать уже добавленные записи из писем">Пересобрать заново</button>
    </div>
    <div id="act-watch"></div>
    <div id="act-list" class="act-wrap">${activityList(items)}</div>
    <div class="act-add">
      <textarea id="act-new" placeholder="Добавить запись в ход действий…"></textarea>
      <button class="btn primary" id="act-add-btn">Добавить</button>
    </div>
    <div class="modal-actions">
      ${isTask ? `<button class="btn" id="act-to-task">← К задаче</button>` : ""}
      <button class="btn" id="act-close">Закрыть</button>
    </div>`);

  function bindItems() {
    document.querySelectorAll("#act-list [data-actdel]").forEach((el) => {
      el.onclick = async () => {
        await api(`/api/activity/${el.dataset.actdel}`, { method: "DELETE" });
        reload();
      };
    });
    document.querySelectorAll("#act-list [data-actmail]").forEach((el) => {
      el.onclick = () => { closeModal(); location.hash = "#/mail"; setTimeout(() => openMail(Number(el.dataset.actmail)), 350); };
    });
    document.querySelectorAll("#act-list [data-acttask]").forEach((el) => {
      el.onclick = async () => {
        let t = state.tasks.find((x) => x.id == el.dataset.acttask);
        if (!t) { state.tasks = await api("/api/tasks"); t = state.tasks.find((x) => x.id == el.dataset.acttask); }
        closeModal();
        if (t) openTaskModal(t); else render();
      };
    });
  }

  $("#act-close").onclick = () => { closeModal(); render(); };
  if (isTask) $("#act-to-task").onclick = async () => {
    let t = state.tasks.find((x) => x.id === obj.id);
    if (!t) { state.tasks = await api("/api/tasks"); t = state.tasks.find((x) => x.id === obj.id); }
    closeModal(); if (t) openTaskModal(t); else render();
  };
  $("#act-add-btn").onclick = async () => {
    const text = $("#act-new").value.trim();
    if (!text) return toast("Введите текст");
    await api(base, { method: "POST", body: JSON.stringify({ body: text }) });
    $("#act-new").value = "";
    toast("Добавлено в ход действий");
    reload();
  };
  const collect = async (force) => {
    $("#act-list").innerHTML = `<div class="empty">${ICON.bot} ИИ разбирает переписку… это может занять до минуты</div>`;
    try {
      const r = await api(`${base}/from-mail`, {
        method: "POST", body: JSON.stringify({ force }),
      });
      items = r.items || [];
      toast(r.added ? `Добавлено записей: ${r.added}${r.ai ? " (с ИИ-разбором)" : ""}` : "Новых писем не найдено");
      draw();
      markSeen();
      reloadWatches();
    } catch (e) {
      $("#act-list").innerHTML = `<div class="error">${esc(e.message)}</div>`;
    }
  };
  $("#act-from-mail").onclick = () => collect(false);
  $("#act-force").onclick = () => collect(true);
  draw();
  markSeen();
  reloadWatches();
}

/* ============================ mail ============================ */
async function viewMail() {
  const days = [[0, "За всё время"], [90, "3 месяца"], [30, "30 дней"], [7, "7 дней"]];
  return `
    <h1 class="page-title">Почта → задачи</h1>
    <p class="page-sub">Подключённая почта · поиск и сортировка по всем письмам</p>
    <div class="tabs">
      <div class="tab ${state.mailTab === "unread" ? "active" : ""}" data-tab="unread"><span class="dot blue"></span>Непрочитанные <span class="badge" id="cnt-unread">…</span></div>
      <div class="tab ${state.mailTab === "starred" ? "active" : ""}" data-tab="starred">${ICON.star}Избранные <span class="badge" id="cnt-starred">…</span></div>
      <div class="tab ${state.mailTab === "sent" ? "active" : ""}" data-tab="sent"><span class="mfolder">→</span>Отправленные <span class="badge" id="cnt-sent">…</span></div>
      <div class="tab ${state.mailTab === "recent" ? "active" : ""}" data-tab="recent">${ICON.mail}Все письма <span class="badge" id="cnt-total">…</span></div>
    </div>
    <div class="toolbar mail-tools">
      <input class="input" id="mail-q" placeholder="Поиск по теме или отправителю…" value="${esc(state.mailQuery)}" style="flex:1;min-width:220px">
      <button class="btn" id="mail-go">Найти</button>
      <select class="select" id="mail-days">
        ${days.map(([v, t]) => `<option value="${v}" ${state.mailDays == v ? "selected" : ""}>${t}</option>`).join("")}
      </select>
      <select class="select" id="mail-sort">
        <option value="newest" ${state.mailSort === "newest" ? "selected" : ""}>Сначала новые</option>
        <option value="oldest" ${state.mailSort === "oldest" ? "selected" : ""}>Сначала старые</option>
      </select>
      <button class="btn" id="mail-sync">⟳ Синхронизировать</button>
      <span id="mail-syncstat" class="muted small"></span>
    </div>
    <div id="mail-host"><div class="empty">Загрузка…</div></div>
    <div class="pager" id="mail-pager"></div>`;
}

async function bindMail() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.onclick = () => { state.mailTab = tab.dataset.tab; state.mailPage = 1; render(); };
  });
  $("#mail-q").oninput = () => { state.mailQuery = $("#mail-q").value; };
  $("#mail-q").onkeydown = (e) => { if (e.key === "Enter") { state.mailPage = 1; render(); } };
  $("#mail-go").onclick = () => { state.mailQuery = $("#mail-q").value; state.mailPage = 1; render(); };
  $("#mail-days").onchange = (e) => { state.mailDays = Number(e.target.value); state.mailPage = 1; render(); };
  $("#mail-sort").onchange = (e) => { state.mailSort = e.target.value; state.mailPage = 1; render(); };

  const loadCounts = async () => {
    try {
      const c = await api("/api/mail/counts");
      const set = (id, v) => { const el = $("#" + id); if (el) el.textContent = v; };
      set("cnt-unread", c.unread); set("cnt-starred", c.starred);
      set("cnt-sent", c.sent ?? 0); set("cnt-total", c.total);
      if (c.sync && c.sync.last_sync) {
        const st = $("#mail-syncstat");
        if (st) st.textContent = c.sync.running
          ? `синхронизация ${c.sync.done}/${c.sync.total}…`
          : `синхр. ${fmtDateTime(c.sync.last_sync)}`;
      }
    } catch (e) {}
  };

  const loadList = async () => {
    try {
      const q = new URLSearchParams({
        tab: state.mailTab, query: state.mailQuery || "",
        days: state.mailDays, sort: state.mailSort,
        page: state.mailPage, page_size: 50,
      });
      const r = await api(`/api/mail/list?${q}`);
      $("#mail-host").innerHTML = r.items.length
        ? `<div class="tasklist">${r.items.map(mailRow).join("")}</div>`
        : `<div class="empty">${state.mailQuery ? "Ничего не найдено" : "Писем нет"}</div>`;
      document.querySelectorAll(".mailrow").forEach((row) => {
        row.onclick = () => openMail(Number(row.dataset.uid));
      });
      $("#mail-pager").innerHTML = r.pages > 1
        ? `<button class="btn small" ${r.page <= 1 ? "disabled" : ""} data-pg="${r.page - 1}">‹ Назад</button>
           <span>Стр. ${r.page} из ${r.pages} · всего ${r.total}</span>
           <button class="btn small" ${r.page >= r.pages ? "disabled" : ""} data-pg="${r.page + 1}">Вперёд ›</button>`
        : `<span>Писем: ${r.total}</span>`;
      document.querySelectorAll("#mail-pager [data-pg]").forEach((b) => {
        b.onclick = () => { state.mailPage = Number(b.dataset.pg); loadList(); window.scrollTo(0, 0); };
      });
    } catch (e) {
      $("#mail-host").innerHTML = `<div class="error">Не удалось загрузить почту: ${esc(e.message)}</div>`;
    }
  };

  $("#mail-sync").onclick = async () => {
    $("#mail-syncstat").textContent = "запуск синхронизации…";
    await api("/api/mail/sync", { method: "POST" });
    const poll = async () => {
      try {
        const s = await api("/api/mail/sync-status");
        const st = $("#mail-syncstat");
        if (!st) return;
        if (s.running) { st.textContent = `синхронизация ${s.done}/${s.total}…`; setTimeout(poll, 2000); }
        else { st.textContent = s.error ? `ошибка: ${s.error}` : `синхр. ${fmtDateTime(s.last_sync || null)}`; loadCounts(); loadList(); }
      } catch (e) {}
    };
    poll();
  };

  await loadCounts();
  await loadList();
  if (state.mailTimer) clearInterval(state.mailTimer);
  state.mailTimer = setInterval(() => {
    if (document.getElementById("mail-host")) { loadList(); loadCounts(); }
    else { clearInterval(state.mailTimer); state.mailTimer = null; }
  }, 60000);
}

function mailRow(m) {
  const sent = m.folder && m.folder !== "INBOX";
  return `<div class="mailrow ${m.seen ? "" : "unread"}" data-uid="${m.uid}">
    <div class="mfrom">${m.seen ? "" : `<span class="dot blue" title="Непрочитано"></span>`}${m.flagged ? ICON.starFill : ""}<span class="who">${esc(m.sender)}</span></div>
    <div class="msubj">${sent ? `<span class="mfolder" title="Отправленные">→</span>` : ""}${esc(m.subject)}</div>
    <div class="mdate">${fmtDateTime(m.date)}</div>
  </div>`;
}

async function openMail(uid) {
  const host = $("#mail-host");
  host.innerHTML = `<div class="empty">Открываю письмо…</div>`;
  try {
    const m = await api(`/api/mail/message/${uid}`);
    await ensureData();
    const projOpts = `<option value="">Без проекта</option>` +
      state.projects.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    const taskOpts = state.tasks.filter((t) => t.status !== "done")
      .map((t) => `<option value="${t.id}">#${t.id} ${esc(t.title)}</option>`).join("");
    const atts = (m.attachments || []);
    host.innerHTML = `
      <button class="btn" id="mail-back">${ICON.back} К списку</button>
      <div class="mailbody" style="margin-top:10px">
        <div class="muted" style="font-size:12.5px;margin-bottom:6px">${m.folder && m.folder !== "INBOX" ? `<span class="mfolder" title="Отправленные">→</span> Отправленные · ` : ""}${esc(m.sender)} · ${fmtDateTime(m.date)}</div>
        <div style="font-size:18px;font-weight:700;margin-bottom:12px">${esc(m.subject)}</div>
        <div class="actions">
          <select class="select" id="m-proj">${projOpts}</select>
          <button class="btn primary" id="mail-task">+ Задача из письма</button>
          <button class="btn" id="mail-link-proj">Привязать к проекту</button>
          <select class="select" id="m-task">${taskOpts}</select>
          <button class="btn" id="mail-link-task">Привязать к задаче</button>
          <button class="btn" id="mail-ai">${ICON.bot} Разобрать письмо</button>
        </div>
        <div id="ai-result"></div>
        ${atts.length ? `
          <div class="atts"><b>Вложения:</b>
            <ul>${atts.map((a) => `<li>${esc(a.filename)} <span class="muted">(${Math.round((a.size || 0) / 1024)} КБ)</span></li>`).join("")}</ul>
            <div class="muted" style="font-size:12.5px">Файлы сохраняются в папку проекта/задачи при привязке.</div>
          </div>` : ""}
        <pre>${esc(m.body || "(пустое письмо)")}</pre>
      </div>`;
    $("#mail-back").onclick = () => render();

    const after = (msg) => {
      toast(msg);
      state.tasks = []; state.projects = [];
    };
    const logMsg = (r) => {
      const l = r.activity_log || r.activity || {};
      return l.added ? ` · в ход действий записей: ${l.added}${l.ai ? " (ИИ)" : ""}` : "";
    };

    $("#mail-task").onclick = async () => {
      const pid = $("#m-proj").value;
      const t = await api(`/api/mail/${uid}/to-task`, {
        method: "POST",
        body: JSON.stringify({ project_id: pid ? Number(pid) : null }),
      });
      after(`Задача #${t.id} создана${t.saved_attachments?.length ? ` · вложений: ${t.saved_attachments.length}` : ""}${logMsg(t)}`);
    };

    $("#mail-link-proj").onclick = async () => {
      const pid = $("#m-proj").value;
      if (!pid) return toast("Выберите проект");
      const r = await api(`/api/mail/${uid}/link`, {
        method: "POST", body: JSON.stringify({ project_id: Number(pid) }),
      });
      toast(`Письмо привязано к проекту${r.saved_attachments?.length ? ` · вложений: ${r.saved_attachments.length}` : ""}${logMsg(r)}`);
    };

    $("#mail-link-task").onclick = async () => {
      const tid = $("#m-task").value;
      if (!tid) return toast("Выберите задачу");
      const r = await api(`/api/mail/${uid}/link`, {
        method: "POST", body: JSON.stringify({ task_id: Number(tid) }),
      });
      toast(`Письмо привязано к задаче${r.saved_attachments?.length ? ` · вложений: ${r.saved_attachments.length}` : ""}${logMsg(r)}`);
    };

    $("#mail-ai").onclick = async () => {
      $("#ai-result").innerHTML = `<div class="ai-block"><div class="empty">ИИ разбирает письмо…</div></div>`;
      try {
        const r = await api(`/api/ai/analyze-mail/${uid}`, { method: "POST" });
        $("#ai-result").innerHTML = r.ok
          ? `<div class="ai-block"><b>Разбор письма</b><pre>${esc(r.analysis)}</pre></div>`
          : `<div class="ai-block error">ИИ недоступен: ${esc(r.error || "")}</div>`;
      } catch (e) {
        $("#ai-result").innerHTML = `<div class="ai-block error">${esc(e.message)}</div>`;
      }
    };
  } catch (e) {
    host.innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}

/* ============================ ai ============================ */
async function viewAI() {
  const st = await api("/api/ai/status").catch(() => ({ enabled: false }));
  const convs = await api("/api/ai/conversations").catch(() => []);
  if (!convs.length) state.aiConv = null;
  if (state.aiConv === null && convs.length) state.aiConv = convs[0].id;
  const warn = st.enabled ? "" : `
    <div class="error" style="margin-bottom:14px">
      ИИ отключён: ключ DeepSeek не задан в config.json → ai.api_key
    </div>`;
  return `
    <h1 class="page-title">ИИ-помощник</h1>
    <p class="page-sub">Консультант и секретарь · ${esc(st.model || "DeepSeek")}</p>
    ${warn}
    <div class="ai-wrap">
      <div class="ai-side">
        <button class="btn primary" id="ai-new" style="width:100%">+ Новый диалог</button>
        <div class="notes-list" id="ai-convs" style="margin-top:10px">
          ${convs.map((c) => `
            <div class="note-item ${c.id === state.aiConv ? "active" : ""}" data-id="${c.id}">
              <div class="nt">${esc(c.name || "Диалог")}</div>
              <div class="nd">${fmtDateTime(c.updated)}</div></div>`).join("") || `<div class="empty">Нет диалогов</div>`}
        </div>
        <button class="btn" id="ai-plan" style="width:100%;margin-top:10px">${ICON.list} План дня</button>
      </div>
      <div class="ai-main">
        <div class="ai-msgs" id="ai-msgs"><div class="empty">Загрузка…</div></div>
        <div class="ai-inputbar">
          <input class="input" id="ai-input" placeholder="Спросите о задачах, сроках, почте…" style="flex:1">
          <button class="btn primary" id="ai-send">Отправить</button>
        </div>
      </div>
    </div>`;
}

async function bindAI() {
  const ensureConv = async () => {
    if (state.aiConv) return state.aiConv;
    const c = await api("/api/ai/conversations", {
      method: "POST", body: JSON.stringify({ name: "Диалог " + new Date().toLocaleDateString("ru-RU") }),
    });
    state.aiConv = c.id;
    return c.id;
  };

  const renderMsgs = (msgs) => {
    $("#ai-msgs").innerHTML = msgs.length
      ? msgs.map((m) => `
          <div class="ai-msg ${m.role === "user" ? "user" : "bot"}">
            <div class="ai-role">${m.role === "user" ? "Вы" : "Помощник"}</div>
            <div class="ai-text">${esc(m.content)}</div>
          </div>`).join("")
      : `<div class="empty">Начните диалог: спросите про задачи, сроки или почту</div>`;
    $("#ai-msgs").scrollTop = $("#ai-msgs").scrollHeight;
  };

  const load = async () => {
    if (!state.aiConv) { renderMsgs([]); return; }
    const msgs = await api(`/api/ai/conversations/${state.aiConv}/messages`).catch(() => []);
    renderMsgs(msgs);
  };

  const send = async (text) => {
    text = (text || "").trim();
    if (!text) return;
    const cid = await ensureConv();
    const before = await api(`/api/ai/conversations/${cid}/messages`).catch(() => []);
    renderMsgs([...before, { role: "user", content: text }, { role: "assistant", content: "…" }]);
    $("#ai-input").value = "";
    try {
      await api(`/api/ai/conversations/${cid}/messages`, {
        method: "POST", body: JSON.stringify({ content: text }),
      });
      await load();
    } catch (e) {
      renderMsgs([{ role: "assistant", content: "Ошибка: " + e.message }]);
    }
  };

  $("#ai-send").onclick = () => send($("#ai-input").value);
  $("#ai-input").onkeydown = (e) => { if (e.key === "Enter") send($("#ai-input").value); };

  $("#ai-new").onclick = async () => {
    const c = await api("/api/ai/conversations", {
      method: "POST", body: JSON.stringify({ name: "Новый диалог" }),
    });
    state.aiConv = c.id;
    render();
  };

  document.querySelectorAll("#ai-convs .note-item").forEach((el) => {
    el.onclick = async () => { state.aiConv = Number(el.dataset.id); render(); };
  });

  $("#ai-plan").onclick = async () => {
    $("#ai-msgs").innerHTML = `<div class="empty">Секретарь готовит план дня…</div>`;
    try {
      const r = await api("/api/ai/secretary");
      const text = r.ok ? r.plan : `Ошибка: ${r.error || ""}`;
      const msgs = state.aiConv
        ? await api(`/api/ai/conversations/${state.aiConv}/messages`).catch(() => [])
        : [];
      renderMsgs([...msgs, { role: "user", content: "План дня" }, { role: "assistant", content: text }]);
    } catch (e) {
      renderMsgs([{ role: "assistant", content: "Ошибка: " + e.message }]);
    }
  };

  await load();
}

/* ============================ charts ============================ */
async function viewCharts() {
  await ensureData();
  const s = await api("/api/stats");
  const gantt = await api("/api/gantt");

  const barChart = (data, kind) => {
    const max = Math.max(1, ...data.map((d) => d.value));
    return `<div>` + data.map((d) => `
      <div class="bar-line" data-kind="${kind}" data-key="${esc(d.key ?? d.label)}">
        <div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:3px">
          <span style="display:inline-flex;align-items:center;gap:7px">${d.dot ? `<span class="dot ${d.dot}"></span>` : ""}${esc(d.label)}</span><span style="color:var(--muted)">${d.value}</span></div>
        <div class="bar"><i style="width:${(d.value / max) * 100}%;background:${d.color}"></i></div>
      </div>`).join("") + `</div>`;
  };

  const statusData = [
    { key: "todo", dot: "gray", label: "К выполнению", value: s.by_status.todo || 0, color: "#8a8a85" },
    { key: "doing", dot: "blue", label: "В работе", value: s.by_status.doing || 0, color: "#2b4ea2" },
    { key: "done", dot: "green", label: "Готово", value: s.by_status.done || 0, color: "#2e7d51" },
    { key: "cancelled", dot: "gray", label: "Отменено", value: s.by_status.cancelled || 0, color: "#c9c9c4" },
  ];
  const prioData = [
    { key: "high", dot: "red", label: "Высокий", value: s.by_priority.high || 0, color: "#c43d2e" },
    { key: "normal", dot: "amber", label: "Обычный", value: s.by_priority.normal || 0, color: "#a8721a" },
    { key: "low", dot: "gray", label: "Низкий", value: s.by_priority.low || 0, color: "#7c8ba5" },
  ];
  const projData = s.by_project.map((p, i) => ({
    key: p.name, label: p.name, value: p.n,
    color: ["#5b8cff", "#2f9e44", "#f5a623", "#e5484d", "#9b6dff", "#0aa2c0"][i % 6],
  }));

  const lineChart = (points) => {
    if (!points.length) return `<div class="empty">Пока нет выполненных задач</div>`;
    const w = 460, h = 150, pad = 24;
    const max = Math.max(1, ...points.map((p) => p.n));
    const step = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;
    const coords = points.map((p, i) => [pad + i * step, h - pad - (p.n / max) * (h - pad * 2)]);
    const path = coords.map((c, i) => `${i ? "L" : "M"}${c[0]},${c[1]}`).join(" ");
    const dots = coords.map((c) => `<circle cx="${c[0]}" cy="${c[1]}" r="3" fill="#2f9e44"/>`).join("");
    return `<svg viewBox="0 0 ${w} ${h}" style="width:100%">
      <line x1="${pad}" y1="${h - pad}" x2="${w - pad}" y2="${h - pad}" stroke="#e9e9e7"/>
      <path d="${path}" fill="none" stroke="#2f9e44" stroke-width="2"/>${dots}</svg>`;
  };

  const ganttHtml = renderGantt(gantt);

  return `
    <h1 class="page-title">Графики и диаграмма Ганта</h1>
    <p class="page-sub">Аналитика по задачам и срокам проекта</p>
    <div class="cards" style="margin-bottom:20px">
      <div class="card"><div class="num">${s.total}</div><div class="lbl">Всего задач</div></div>
      <div class="card warn"><div class="num">${s.overdue}</div><div class="lbl">Просрочено</div></div>
      <div class="card"><div class="num">${s.upcoming}</div><div class="lbl">Предстоящих</div></div>
      <div class="card good"><div class="num">${s.by_status.done || 0}</div><div class="lbl">Выполнено</div></div>
    </div>
    <div class="chart-grid">
      <div class="chart-box"><h3>По статусам <span class="hint-inline">клик — список задач</span></h3>${barChart(statusData, "status")}</div>
      <div class="chart-box"><h3>По приоритетам <span class="hint-inline">клик — список задач</span></h3>${barChart(prioData, "priority")}</div>
      <div class="chart-box"><h3>По проектам <span class="hint-inline">клик — список задач</span></h3>${projData.length ? barChart(projData, "project") : `<div class="empty">Нет данных</div>`}</div>
      <div class="chart-box"><h3>Выполнено за 14 дней</h3>${lineChart(s.done_by_day)}</div>
    </div>
    <div class="section-title">Диаграмма Ганта <span class="hint-inline">клик по строке — открыть задачу</span></div>
    <div class="chart-box">${ganttHtml}</div>`;
}

function bindCharts() {
  document.querySelectorAll(".gantt-row[data-id]").forEach((row) => {
    row.onclick = () => {
      const t = state.tasks.find((x) => x.id === Number(row.dataset.id));
      if (t) openTaskModal(t);
    };
  });
  document.querySelectorAll(".bar-line").forEach((el) => {
    el.onclick = () => openCategoryModal(el.dataset.kind, el.dataset.key);
  });
}

function openCategoryModal(kind, key) {
  const pname = (id) => {
    const p = state.projects.find((x) => x.id === id);
    return p ? p.name : "Без проекта";
  };
  let list = [], title = key;
  if (kind === "status") {
    list = state.tasks.filter((t) => t.status === key);
    title = "Статус: " + (STITLE[key] || key);
  } else if (kind === "priority") {
    list = state.tasks.filter((t) => (t.priority || "normal") === key);
    title = "Приоритет: " + key;
  } else if (kind === "project") {
    list = state.tasks.filter((t) => pname(t.project_id) === key);
    title = "Проект: " + key;
  }
  openModal(`<h2>${esc(title)}</h2>
    <div class="muted small" style="margin-bottom:8px">Задач: ${list.length}</div>
    ${list.length ? `<div class="tasklist">${list.map(taskRow).join("")}</div>` : `<div class="empty">Задач нет</div>`}`);
  document.querySelectorAll("#modal [data-edit]").forEach((el) => {
    el.onclick = () => {
      const t = state.tasks.find((x) => x.id === Number(el.dataset.edit));
      if (t) { closeModal(); openTaskModal(t); }
    };
  });
  document.querySelectorAll("#modal [data-toggle]").forEach((el) => {
    el.onclick = async () => {
      const t = state.tasks.find((x) => x.id === Number(el.dataset.toggle));
      if (!t) return;
      await api(`/api/tasks/${t.id}`, { method: "PATCH", body: JSON.stringify({ status: t.status === "done" ? "todo" : "done" }) });
      state.tasks = [];
      closeModal();
      render();
    };
  });
}

function renderGantt(rows) {
  if (!rows.length) return `<div class="empty">Нет задач со сроками</div>`;
  const dates = [];
  rows.forEach((r) => { [r.start_date, r.deadline].forEach((d) => { if (d) dates.push(new Date(d.slice(0, 10))); }); });
  const min = new Date(Math.min(...dates)), max = new Date(Math.max(...dates));
  const span = Math.max(1, (max - min) / 86400000);
  const body = rows.map((r) => {
    const s = r.start_date ? new Date(r.start_date.slice(0, 10)) : (r.deadline ? new Date(r.deadline.slice(0, 10)) : new Date());
    const e = r.deadline ? new Date(r.deadline.slice(0, 10)) : s;
    const left = ((s - min) / 86400000 / span) * 100;
    const width = Math.max(1.5, ((e - s) / 86400000 / span) * 100);
    const cls = r.status === "done" ? "done" : (r.priority === "high" ? "high" : "");
    return `<div class="gantt-row" data-id="${r.id}" title="${esc(r.title)}">
      <div class="gantt-label" title="${esc(r.title)}">${esc(r.title)}</div>
      <div class="gantt-track"><div class="gantt-bar ${cls}" style="left:${left}%;width:${width}%"></div></div>
    </div>`;
  }).join("");
  const months = [];
  const cur = new Date(min); cur.setDate(1);
  while (cur <= max) { months.push(cur.toLocaleDateString("ru-RU", { month: "short", year: "2-digit" })); cur.setMonth(cur.getMonth() + 1); }
  return `<div class="gantt">${body}
    <div class="gantt-axis">${(months.length ? months : [""]).map((m) => `<span>${m}</span>`).join("")}</div></div>`;
}

/* ============================ notes ============================ */
async function viewNotes() {
  const notes = await api("/api/notes");
  if (!notes.length) {
    const n = await api("/api/notes", { method: "POST", body: JSON.stringify({ title: "Первая заметка", content: "" }) });
    notes.push(n);
  }
  if (!state.noteId || !notes.find((n) => n.id === state.noteId)) state.noteId = notes[0].id;
  return `
    <h1 class="page-title">Заметки</h1>
    <p class="page-sub">Свободные страницы для мыслей и черновиков</p>
    <div class="toolbar"><button class="btn primary" id="add-note">+ Заметка</button></div>
    <div class="notes-wrap">
      <div class="notes-list" id="notes-list">
        ${notes.map((n) => `<div class="note-item ${n.id === state.noteId ? "active" : ""}" data-id="${n.id}">
          <div class="nt">${esc(n.title || "Без названия")}</div>
          <div class="nd">${fmtDateTime(n.updated)}</div></div>`).join("")}
      </div>
      <div class="note-editor" id="note-editor"></div>
    </div>`;
}

async function bindNotes() {
  const loadEditor = async (id) => {
    const all = await api("/api/notes");
    const note = all.find((x) => x.id === id) || all[0];
    if (!note) { $("#note-editor").innerHTML = `<div class="empty">Заметок нет</div>`; return; }
    $("#note-editor").innerHTML = `
      <input class="title" id="n-title" value="${esc(note.title)}" placeholder="Заголовок">
      <textarea id="n-content" placeholder="Текст заметки…">${esc(note.content)}</textarea>
      <div style="display:flex;justify-content:space-between;margin-top:12px">
        <button class="btn" id="n-del">Удалить</button>
        <span style="color:var(--muted);font-size:12px" id="n-saved">изменено ${fmtDateTime(note.updated)}</span>
      </div>`;
    let timer;
    const save = async () => {
      await api(`/api/notes/${note.id}`, { method: "PATCH", body: JSON.stringify({ title: $("#n-title").value, content: $("#n-content").value }) });
      $("#n-saved").textContent = "сохранено " + new Date().toLocaleTimeString("ru-RU");
    };
    $("#n-title").oninput = $("#n-content").oninput = () => { clearTimeout(timer); timer = setTimeout(save, 700); };
    $("#n-del").onclick = async () => {
      if (!confirm("Удалить заметку?")) return;
      await api(`/api/notes/${note.id}`, { method: "DELETE" });
      state.noteId = null; render();
    };
  };
  document.querySelectorAll(".note-item").forEach((el) => {
    el.onclick = () => { state.noteId = Number(el.dataset.id); render(); };
  });
  $("#add-note").onclick = async () => {
    const n = await api("/api/notes", { method: "POST", body: JSON.stringify({ title: "Новая заметка", content: "" }) });
    state.noteId = n.id; render();
  };
  await loadEditor(state.noteId);
}

/* ============================ router ============================ */
const ROUTES = {
  overview: viewOverview, tasks: viewTasks, kanban: viewKanban,
  calendar: viewCalendar, projects: viewProjects, mail: viewMail,
  ai: viewAI, charts: viewCharts, notes: viewNotes,
};

function currentView() {
  const m = location.hash.match(/^#\/(\w+)/);
  return m && ROUTES[m[1]] ? m[1] : "overview";
}

const BINDERS = {
  tasks: bindTasks, kanban: bindKanban, calendar: bindCalendar,
  projects: bindProjects, mail: bindMail, ai: bindAI, charts: bindCharts, notes: bindNotes,
};

async function render() {
  const view = currentView();
  if (state.mailTimer) { clearInterval(state.mailTimer); state.mailTimer = null; }
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  const host = $("#view");
  try {
    host.innerHTML = await ROUTES[view]();
    if (BINDERS[view]) await BINDERS[view]();
    bindGlobalHandlers();
    applyBadges();
  } catch (e) {
    host.innerHTML = `<div class="error">Ошибка: ${esc(e.message)}</div>`;
  }
}

function bindGlobalHandlers() {
  document.querySelectorAll("[data-toggle]").forEach((el) => {
    el.onclick = async (e) => {
      e.stopPropagation();
      const id = el.dataset.toggle;
      const t = state.tasks.find((x) => x.id == id);
      const status = t.status === "done" ? "todo" : "done";
      await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
      state.tasks = []; state.projects = []; render();
    };
  });
  document.querySelectorAll("[data-edit]").forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();
      const t = state.tasks.find((x) => x.id == el.dataset.edit);
      openTaskModal(t);
    };
  });
  document.querySelectorAll("[data-open]").forEach((el) => {
    el.onclick = () => {
      const t = state.tasks.find((x) => x.id == el.dataset.open);
      if (t) openTaskModal(t);
    };
  });
}

/* ============================ boot ============================ */
window.addEventListener("hashchange", render);
window.addEventListener("DOMContentLoaded", () => {
  bindBell();
  if (!location.hash) location.hash = "#/overview";
  render();
  setInterval(pollAlerts, 45000);
});

