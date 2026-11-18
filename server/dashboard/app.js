const stateLabels = {
  offline: "Offline", idle: "Připravená", queued: "Ve frontě", assigned: "Přiděleno",
  downloading: "Stahuje G-code", ready: "Připraveno", heating: "Nahřívá",
  leveling: "Leveluje", waiting_local_approval: "Čeká na potvrzení",
  printing: "Tiskne", paused: "Pozastaveno", complete: "Dokončeno", error: "Chyba"
};

let adminKey = sessionStorage.getItem("farmAdminKey") || "";
let refreshTimer;
const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Admin-Key", adminKey);
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 || response.status === 503) throw new Error("AUTH");
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function effectiveStatus(printer) {
  if (!printer.last_seen) return "offline";
  const age = Date.now() - new Date(printer.last_seen).getTime();
  return age > 30000 ? "offline" : printer.status;
}

function temp(current, target) {
  const c = current == null ? "—" : `${Math.round(current)}°`;
  return target > 0 ? `${c} / ${Math.round(target)}°` : c;
}

function relativeTime(value) {
  if (!value) return "bez spojení";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 10) return "právě teď";
  if (seconds < 60) return `před ${seconds} s`;
  return `před ${Math.floor(seconds / 60)} min`;
}

function renderPrinters(printers) {
  const root = $("#printers");
  root.replaceChildren();
  const counts = { printing: 0, waiting: 0, idle: 0, offline: 0 };
  printers.forEach((printer) => {
    const status = effectiveStatus(printer);
    if (status === "printing") counts.printing++;
    else if (status === "waiting_local_approval") counts.waiting++;
    else if (status === "offline") counts.offline++;
    else if (["idle", "ready", "complete"].includes(status)) counts.idle++;

    const card = $("#printer-template").content.firstElementChild.cloneNode(true);
    if (status === "waiting_local_approval") card.classList.add("waiting");
    card.querySelector(".printer-name").textContent = printer.name;
    card.querySelector(".printer-state").textContent = stateLabels[status] || status;
    card.querySelector(".last-seen").textContent = relativeTime(printer.last_seen);
    const dot = card.querySelector(".status-dot");
    dot.classList.add(status === "waiting_local_approval" ? "waiting" : status);
    const progress = Math.min(100, Math.max(0, Number(printer.progress) || 0));
    card.querySelector(".progress-value").textContent = `${progress.toFixed(0)} %`;
    card.querySelector(".progress-bar").style.width = `${progress}%`;
    card.querySelector(".hotend").textContent = temp(printer.hotend_current, printer.hotend_target);
    card.querySelector(".bed").textContent = temp(printer.bed_current, printer.bed_target);
    root.append(card);
  });
  $("#empty-printers").style.display = printers.length ? "none" : "block";
  $("#printer-count").textContent = `${printers.length} / 40`;
  Object.entries(counts).forEach(([key, value]) => $(`#count-${key}`).textContent = value);
}

function renderJobs(jobs, printers) {
  const body = $("#jobs");
  const printerNames = new Map(printers.map((p) => [p.id, p.name]));
  body.replaceChildren();
  jobs.forEach((job) => {
    const row = document.createElement("tr");
    const values = [
      `#${job.queue_position}`, job.original_name, stateLabels[job.status] || job.status,
      printerNames.get(job.printer_id) || "—", `${(job.size_bytes / 1024 / 1024).toFixed(2)} MB`
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      if (index === 2) { const badge = document.createElement("span"); badge.className = "queue-state"; badge.textContent = value; cell.append(badge); }
      else cell.textContent = value;
      row.append(cell);
    });
    body.append(row);
  });
  $("#empty-jobs").style.display = jobs.length ? "none" : "block";
}

async function refresh() {
  if (!adminKey) return;
  try {
    const [printers, jobs] = await Promise.all([api("/api/printers"), api("/api/jobs")]);
    renderPrinters(printers);
    renderJobs(jobs, printers);
    $("#connection").textContent = "Server online";
    $("#connection").classList.add("online");
    $("#login").classList.add("hidden");
  } catch (error) {
    $("#connection").textContent = "Server offline";
    $("#connection").classList.remove("online");
    if (error.message === "AUTH") showLogin("Neplatný klíč nebo server není nakonfigurován.");
  }
}

function showLogin(message = "") {
  clearInterval(refreshTimer);
  $("#login").classList.remove("hidden");
  $("#login-error").textContent = message;
}

async function upload(file) {
  if (!file) return;
  if (!/\.(gcode|gco|gc)$/i.test(file.name)) { $("#upload-status").textContent = "Vyberte soubor G-code."; return; }
  const form = new FormData();
  form.append("file", file);
  $("#upload-status").textContent = `Nahrávám ${file.name}…`;
  try {
    const result = await api("/api/jobs", { method: "POST", body: form });
    $("#upload-status").textContent = `${result.name} byl přidán na pozici ${result.queue_position}.`;
    await refresh();
  } catch (error) { $("#upload-status").textContent = `Nahrání selhalo: ${error.message}`; }
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  adminKey = $("#admin-key").value;
  sessionStorage.setItem("farmAdminKey", adminKey);
  await refresh();
  if ($("#login").classList.contains("hidden")) refreshTimer = setInterval(refresh, 3000);
});
$("#logout").addEventListener("click", () => { sessionStorage.removeItem("farmAdminKey"); adminKey = ""; showLogin(); });
$("#refresh").addEventListener("click", refresh);
const dropzone = $("#dropzone");
const fileInput = $("#file-input");
dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) fileInput.click(); });
fileInput.addEventListener("change", () => upload(fileInput.files[0]));
["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add("dragging"); }));
["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove("dragging"); }));
dropzone.addEventListener("drop", (event) => upload(event.dataTransfer.files[0]));

if (adminKey) { refresh(); refreshTimer = setInterval(refresh, 3000); } else showLogin();
