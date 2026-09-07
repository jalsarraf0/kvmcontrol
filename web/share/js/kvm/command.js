/* SPDX-License-Identifier: GPL-3.0-or-later */
"use strict";

const $ = id => document.getElementById(id);
const storagePrefix = "glkvm.command.";
let scheduler = null;
let atx = null;
let refreshing = false;
let diagnostics = {};
let noticeTimer = null;
let customShortcuts = [];
const defaults = [
    {name: "Ctrl + Alt + Del", keys: "ControlLeft,AltLeft,Delete"},
    {name: "Boot · F12", keys: "F12"},
    {name: "BIOS · F2", keys: "F2"},
    {name: "Escape", keys: "Escape"},
    {name: "Task manager", keys: "ControlLeft,ShiftLeft,Escape"},
];

function notice(message, error = false) {
    clearTimeout(noticeTimer);
    $("notice").textContent = message;
    $("notice").className = error ? "error" : "";
    $("notice").hidden = false;
    if (!error) noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 8000);
}

async function api(path, body, query) {
    const url = new URL(`/api/${path}`, location.origin);
    if (query) url.search = new URLSearchParams(query).toString();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
        const response = await fetch(url, {
            credentials: "same-origin", cache: "no-store", signal: controller.signal,
            method: body === undefined ? "GET" : "POST",
            headers: body === undefined ? {} : {"Content-Type": "application/json"},
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        if (response.status === 401 || response.status === 403) throw new Error("Session expired or access denied. Open the console to sign in, then refresh.");
        const data = await response.json().catch(() => null);
        if (!response.ok || !data || data.ok === false) {
            throw new Error(data?.result?.error_msg || data?.error || `Request failed (${response.status})`);
        }
        return data.result;
    } catch (error) {
        if (error.name === "AbortError") throw new Error("Request timed out. Its outcome may be unknown; refresh before retrying.");
        throw error;
    } finally { clearTimeout(timer); }
}

async function perform(button, action) {
    button.disabled = true;
    try { await action(); }
    catch (error) { notice(error.message, true); }
    finally { button.disabled = false; }
}

function confirmAction(title, detail) {
    const dialog = $("confirm-dialog");
    if (dialog.open) return Promise.resolve(false);
    $("confirm-title").textContent = title;
    $("confirm-detail").textContent = detail;
    dialog.returnValue = "cancel";
    dialog.showModal();
    dialog.querySelector('[value="cancel"]').focus();
    return new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true}));
}

function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
}

function download(name, text, type = "application/json") {
    const url = URL.createObjectURL(new Blob([text], {type}));
    const link = node("a");
    link.href = url; link.download = name;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function readLocal(key, fallback) {
    try { return localStorage.getItem(storagePrefix + key) ?? fallback; }
    catch (_) { notice("Browser storage is unavailable; notes and custom shortcuts will not persist.", true); return fallback; }
}
function saveLocal(key, value) {
    try { localStorage.setItem(storagePrefix + key, value); return true; }
    catch (_) { notice("Could not save browser data. Export your notes before leaving.", true); return false; }
}

function formatDate(value) { return new Date(value).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}); }
function powerState(state) {
    if (!state?.enabled) return "Unavailable";
    if (state.busy) return "Busy";
    const power = state.power ?? state.leds?.power;
    return power === true || power === "on" ? "On" : power === false || power === "off" ? "Off" : "Unknown";
}

function renderSchedules() {
    const jobs = [...scheduler.jobs].sort((a, b) => new Date(a.at) - new Date(b.at));
    $("armed-state").textContent = scheduler.error ? "Locked" : scheduler.armed ? "Enabled" : "Paused";
    $("job-count").textContent = `${jobs.filter(job => job.enabled).length} active / ${jobs.length} saved schedules`;
    $("arm-toggle").textContent = scheduler.armed ? "Pause all scheduling" : "Enable scheduling";
    $("arm-toggle").className = scheduler.armed ? "danger" : "primary";
    $("arm-toggle").disabled = Boolean(scheduler.error);
    $("schedule-form").querySelector('[type="submit"]').disabled = Boolean(scheduler.error);
    $("jobs").replaceChildren();
    if (!jobs.length) $("jobs").append(node("p", "Your queue is clear. Add a power schedule to get started.", "empty"));
    for (const job of jobs) {
        const row = node("article", undefined, "job");
        const heading = node("div", undefined, "panel-heading");
        heading.append(node("h4", job.name), node("span", job.enabled ? "QUEUED" : "INACTIVE", "pill"));
        const actions = {on: "Power on · ATX", off: "Shut down · ATX", wol: "Wake-on-LAN"};
        const repeat = {once: "One time", daily: "Every 24 hours", weekly: "Every 7 days"};
        row.append(heading, node("p", `${actions[job.action]} · ${repeat[job.repeat]}`), node("p", formatDate(job.at)));
        if (job.mac) row.append(node("p", job.mac));
        const controls = node("div", undefined, "job-actions");
        for (const operation of [job.enabled ? "pause" : "resume", "delete"]) {
            const button = node("button", operation[0].toUpperCase() + operation.slice(1));
            button.disabled = Boolean(scheduler.error);
            button.addEventListener("click", () => perform(button, async () => {
                if (operation === "delete" && !await confirmAction("Delete schedule?", job.name)) return;
                await api("scheduler/job", {id: job.id, operation});
                await refresh();
            }));
            controls.append(button);
        }
        row.append(controls); $("jobs").append(row);
    }
    $("history").replaceChildren();
    if (!scheduler.history.length) $("history").append(node("p", "No scheduled activity yet.", "empty"));
    for (const event of scheduler.history) {
        const row = node("div", undefined, "event");
        const when = node("time", formatDate(event.time)); when.dateTime = event.time;
        const detail = node("div"); detail.append(node("strong", event.name), node("small", event.detail));
        row.append(when, node("span", event.status.toUpperCase(), event.status), detail);
        $("history").append(row);
    }
    tick();
}

async function refresh() {
    if (refreshing) return;
    refreshing = true;
    const started = performance.now();
    const results = await Promise.allSettled([api("scheduler"), api("atx")]);
    const schedulerResult = results[0];
    const powerResult = results[1];
    if (schedulerResult.status === "fulfilled") {
        scheduler = schedulerResult.value;
        renderSchedules();
        if (scheduler.error) notice(scheduler.error, true);
    } else {
        scheduler = null;
        $("armed-state").textContent = "Unavailable";
        $("job-count").textContent = "Scheduler status could not be read";
        $("arm-toggle").disabled = true;
        $("schedule-form").querySelector('[type="submit"]').disabled = true;
        $("jobs").replaceChildren(node("p", "Cannot load schedules. Refresh after reconnecting.", "empty"));
        notice(schedulerResult.reason.message, true);
    }
    atx = powerResult.status === "fulfilled" ? powerResult.value : null;
    const power = powerState(atx);
    $("power-state").textContent = power;
    $("atx-detail").textContent = atx?.enabled ? "ATX accessory available" : "ATX unavailable or not connected";
    $("power-on").disabled = !atx?.enabled || atx.busy || power === "On" || power === "Unknown";
    $("power-off").disabled = !atx?.enabled || atx.busy || power === "Off" || power === "Unknown";
    const online = results.every(result => result.status === "fulfilled");
    $("connection").textContent = online ? "● CONNECTED" : "● DEGRADED";
    $("connection").className = `pill ${online ? "online" : "offline"}`;
    diagnostics = {host: location.host, checked_at: new Date().toISOString(), api: online ? "Connected" : "Partial / unavailable", request_ms: Math.round(performance.now() - started), power,
        scheduler: scheduler ? (scheduler.error ? "Locked" : scheduler.armed ? "Enabled" : "Paused") : "Unavailable",
        clock_offset_seconds: scheduler ? Math.round(scheduler.server_time - Date.now() / 1000) : null};
    $("diagnostics").replaceChildren();
    for (const [key, value] of Object.entries({API: diagnostics.api, "Refresh time": `${diagnostics.request_ms} ms`, "ATX power": power, Scheduler: diagnostics.scheduler, "Clock offset": diagnostics.clock_offset_seconds === null ? "Unknown" : `${diagnostics.clock_offset_seconds} seconds`})) {
        $("diagnostics").append(node("dt", key), node("dd", value));
    }
    if (diagnostics.clock_offset_seconds !== null && Math.abs(diagnostics.clock_offset_seconds) > 30) notice("Browser and KVM clocks differ by more than 30 seconds. Correct the clocks before enabling schedules.", true);
    refreshing = false;
    tick();
}

function tick() {
    $("clock").textContent = new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
    const next = scheduler?.armed && !scheduler.error ? scheduler.jobs.filter(job => job.enabled).sort((a, b) => new Date(a.at) - new Date(b.at))[0] : null;
    $("next-job").textContent = next?.name || "—";
    if (!next) { $("countdown").textContent = scheduler?.armed ? "No active schedules" : "Scheduling is paused or unavailable"; return; }
    const seconds = Math.max(0, Math.ceil((new Date(next.at).getTime() - Date.now()) / 1000));
    $("countdown").textContent = seconds ? `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h ${Math.floor(seconds % 3600 / 60)}m ${seconds % 60}s remaining` : "Due · awaiting scheduler";
}

$("timezone").textContent = Intl.DateTimeFormat().resolvedOptions().timeZone;
$("refresh").addEventListener("click", () => perform($("refresh"), refresh));
$("arm-toggle").addEventListener("click", () => perform($("arm-toggle"), async () => {
    if (!scheduler) return;
    const armed = !scheduler.armed;
    if (armed && !await confirmAction("Enable power scheduling?", "Saved active schedules will run on this KVM, even after you close the browser. Check the target, clock, and ATX wiring first.")) return;
    await api("scheduler/armed", {armed});
    notice(armed ? "Scheduling enabled." : "Scheduling paused. Any command already dispatched may still finish.");
    await refresh();
}));

const scheduleForm = $("schedule-form");
scheduleForm.elements.action.addEventListener("change", () => {
    const wol = scheduleForm.elements.action.value === "wol";
    $("schedule-mac-label").hidden = !wol;
    scheduleForm.elements.mac.required = wol;
});
function setDelay(minutes) {
    const date = new Date(Date.now() + minutes * 60000);
    date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
    scheduleForm.elements.at.value = date.toISOString().slice(0, 16);
}
setDelay(60);
for (const button of document.querySelectorAll("[data-delay]")) button.addEventListener("click", () => setDelay(Number(button.dataset.delay)));
scheduleForm.addEventListener("submit", event => {
    event.preventDefault();
    perform(event.submitter, async () => {
        const body = Object.fromEntries(new FormData(scheduleForm));
        const localDate = new Date(body.at);
        if (Number.isNaN(localDate.getTime())) throw new Error("Choose a valid date and time.");
        body.at = localDate.toISOString();
        if (!await confirmAction("Save power schedule?", `${body.name}: ${body.action} at ${formatDate(body.at)}. ${scheduler?.armed ? "Scheduling is enabled; this job will execute at its due time." : "Scheduling is paused."}`)) return;
        await api("scheduler/jobs", body);
        notice("Schedule saved on the KVM.");
        await refresh();
    });
});

for (const action of ["on", "off"]) $("power-" + action).addEventListener("click", () => perform($("power-" + action), async () => {
    if (!await confirmAction(action === "on" ? "Power on attached host?" : "Shut down attached host?", action === "on" ? "Send an ATX power-on request to the computer connected to this KVM." : "Send a graceful ATX shutdown request. Save your work first; the OS decides how to handle the power button.")) return;
    const state = await api("atx");
    const current = powerState(state);
    if (!state.enabled || state.busy || current === "Unknown") throw new Error("ATX is unavailable, busy, or its power state is unknown.");
    if ((action === "on" && current === "On") || (action === "off" && current === "Off")) { notice("Host is already in the requested state."); return; }
    await api("atx/power", {}, {action, wait: "true"});
    notice("ATX request completed. Host transition is not yet confirmed.");
    await refresh();
}));

$("wake-devices").addEventListener("change", () => { $("wake-form").elements.mac.value = $("wake-devices").value; });
$("wake-form").addEventListener("submit", event => {
    event.preventDefault();
    perform(event.submitter, async () => {
        const mac = $("wake-form").elements.mac.value.trim();
        if (!await confirmAction("Wake LAN device?", `Send a magic packet to ${mac}.`)) return;
        await api("wol/wake", {}, {mac});
        notice("Wake packet sent. Host startup is not confirmed.");
    });
});

function renderShortcuts() {
    $("shortcuts").replaceChildren();
    [...defaults, ...customShortcuts].forEach((shortcut, index) => {
        const item = node("span", undefined, "shortcut-item");
        const button = node("button", shortcut.name); button.title = shortcut.keys;
        button.addEventListener("click", () => perform(button, async () => {
            if (!await confirmAction("Send keyboard shortcut?", `${shortcut.name} (${shortcut.keys}) will be sent to the attached computer.`)) return;
            await api("hid/events/send_shortcut", {}, {keys: shortcut.keys});
            notice("Shortcut sent.");
        }));
        item.append(button);
        if (index >= defaults.length) {
            const remove = node("button", "×"); remove.setAttribute("aria-label", `Remove ${shortcut.name}`);
            remove.addEventListener("click", () => {
                customShortcuts.splice(index - defaults.length, 1);
                saveLocal("shortcuts", JSON.stringify(customShortcuts)); renderShortcuts();
            });
            item.append(remove);
        }
        $("shortcuts").append(item);
    });
}
try {
    const saved = JSON.parse(readLocal("shortcuts", "[]"));
    if (Array.isArray(saved)) customShortcuts = saved.filter(item => item && typeof item.name === "string" && typeof item.keys === "string").slice(0, 20);
} catch (_) { notice("Saved shortcuts could not be read. Built-in shortcuts are still available.", true); }
renderShortcuts();
$("shortcut-form").addEventListener("submit", event => {
    event.preventDefault();
    if (customShortcuts.length >= 20) { notice("Remove a custom shortcut before adding more (limit: 20).", true); return; }
    const values = Object.fromEntries(new FormData(event.target));
    const keys = values.keys.split(",").map(key => key.trim());
    if (!values.name.trim() || keys.length > 8 || keys.some(key => !/^[A-Za-z][A-Za-z0-9]{0,30}$/.test(key))) { notice("Enter a name and 1–8 browser key codes, such as ControlLeft,AltLeft,Delete.", true); return; }
    customShortcuts.push({name: values.name.trim(), keys: keys.join(",")});
    saveLocal("shortcuts", JSON.stringify(customShortcuts)); renderShortcuts(); event.target.reset();
});

$("notes").value = readLocal("notes", "");
let notesTimer;
$("notes").addEventListener("input", () => {
    clearTimeout(notesTimer);
    $("notes-status").textContent = "Saving…";
    notesTimer = setTimeout(() => { $("notes-status").textContent = saveLocal("notes", $("notes").value) ? "Saved in this browser · no passwords" : "Not saved · export your notes"; }, 300);
});
window.addEventListener("pagehide", () => { clearTimeout(notesTimer); saveLocal("notes", $("notes").value); });
$("notes-export").addEventListener("click", () => download("kvm-session-notes.txt", $("notes").value, "text/plain"));
$("export-schedules").addEventListener("click", () => {
    if (!scheduler) { notice("Load schedules before exporting.", true); return; }
    download("kvm-power-schedules.json", JSON.stringify(scheduler, null, 2));
});
$("diagnostics-export").addEventListener("click", () => download("kvm-status.json", JSON.stringify(diagnostics, null, 2)));

const launchActions = [
    ["Launch live console", () => location.assign("/")],
    ["Schedule power on / shutdown", () => document.querySelector("#automation").scrollIntoView()],
    ["Wake-on-LAN", () => $("wake-form").scrollIntoView()],
    ["Keyboard shortcuts", () => $("shortcuts").scrollIntoView()],
    ["Session notebook", () => $("notes").focus()],
    ["Scheduled activity", () => $("history").scrollIntoView()],
    ["Refresh connection status", refresh],
];
function filterLauncher() {
    const query = $("launcher-search").value.toLowerCase();
    $("launcher-results").replaceChildren();
    for (const [label, action] of launchActions.filter(([label]) => label.toLowerCase().includes(query))) {
        const button = node("button", label);
        button.addEventListener("click", () => { $("launcher").close(); action(); });
        $("launcher-results").append(button);
    }
    if (!$("launcher-results").children.length) $("launcher-results").append(node("p", "No matching actions.", "empty"));
}
function openLauncher() { $("launcher-search").value = ""; filterLauncher(); $("launcher").showModal(); $("launcher-search").focus(); }
$("launcher-open").addEventListener("click", openLauncher);
$("launcher-close").addEventListener("click", () => $("launcher").close());
$("launcher-search").addEventListener("input", filterLauncher);
document.addEventListener("keydown", event => {
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.closest("input,textarea,select,[contenteditable],dialog")) { event.preventDefault(); openLauncher(); }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
setInterval(tick, 1000);
setInterval(() => { if (!document.hidden && !document.querySelector("dialog[open]")) refresh(); }, 10000);
refresh();
api("wol/list").then(data => {
    for (const device of data.devices || []) {
        const option = node("option", `${device.name || device.mac} · ${device.mac}`); option.value = device.mac;
        $("wake-devices").append(option);
    }
}).catch(() => { $("wake-devices").replaceChildren(node("option", "Saved devices unavailable · enter MAC manually")); });
