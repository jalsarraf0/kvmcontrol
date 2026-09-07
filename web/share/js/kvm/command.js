/* SPDX-License-Identifier: GPL-3.0-or-later */
"use strict";

const $ = id => document.getElementById(id);
const storagePrefix = "glkvm.command.";
let scheduler = null;
let atx = null;
let fleet = [];
let selectedId = sessionStorage.getItem(storagePrefix + "target") || "local";
let hostname = "";
let restoring = null;
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

function remoteSelected() {
    const target = fleet.find(item => item.id === selectedId);
    return Boolean(target && target.host);
}

async function api(path, body, query) {
    const url = new URL(`/api/${path}`, location.origin);
    if (query) url.search = new URLSearchParams(query).toString();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 50000);
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

async function sched(path, body) {
    const full = "/scheduler" + path;
    if (remoteSelected()) {
        return api("scheduler/fleet", {target: selectedId, path: full, body: body === undefined ? null : body});
    }
    return api("scheduler" + path, body);
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
    $("confirm-target").textContent = `TARGET  ${hostname || selectedId}  ·  ${selectedLabel()}`;
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
function selectedLabel() {
    return fleet.find(item => item.id === selectedId)?.label || hostname || location.host;
}
function civilRepeat(value) {
    return value === "calendar" || value === "weekdays" || value === "weekends";
}

function renderFleet() {
    $("fleet-cards").replaceChildren();
    if (!fleet.length) $("fleet-cards").append(node("p", "Local target only. Optional fleet profiles live in /etc/kvmd/user/kvmcontrol-fleet.json.", "empty"));
    for (const target of fleet) {
        const card = node("button", undefined, "fleet-card" + (target.id === selectedId ? " selected" : ""));
        card.type = "button";
        const state = target.state || {};
        card.append(
            node("h3", target.label),
            node("p", `${target.host ? "SSH " + target.host : "This KVM"} · power ${state.power || "unknown"} · ${target.error || (state.armed ? "armed" : "paused")}`),
        );
        card.addEventListener("click", () => {
            selectedId = target.id;
            sessionStorage.setItem(storagePrefix + "target", selectedId);
            refresh();
        });
        $("fleet-cards").append(card);
    }
    $("target-chip").textContent = "TARGET · " + selectedLabel().toUpperCase();
    const remote = remoteSelected();
    $("hid-hint").textContent = remote
        ? "Keyboard shortcuts are local-only. Open the selected KVM console to send HID."
        : "Every shortcut requires confirmation. Custom shortcuts stay in this browser.";
    for (const button of $("shortcuts").querySelectorAll("button")) button.disabled = remote;
    const peer = fleet.find(item => item.id === selectedId);
    $("remote-console").hidden = !(peer && peer.url && peer.url.startsWith("https://"));
    if (!$("remote-console").hidden) $("remote-console").href = peer.url;
}

function renderPresets() {
    $("presets").replaceChildren();
    for (const preset of scheduler?.presets || []) {
        const button = node("button", preset.name);
        button.type = "button";
        button.addEventListener("click", () => {
            const form = $("schedule-form");
            form.elements.name.value = preset.name;
            form.elements.action.value = preset.action;
            form.elements.repeat.value = preset.repeat;
            if (preset.timezone) form.elements.timezone.value = preset.timezone;
            if (preset.wall_time) form.elements.wall_time.value = preset.wall_time;
            if (preset.mac) form.elements.mac.value = preset.mac;
            updateScheduleForm();
        });
        const remove = node("button", "×");
        remove.type = "button";
        remove.setAttribute("aria-label", "Delete preset " + preset.name);
        remove.addEventListener("click", () => perform(remove, async () => {
            if (!await confirmAction("Delete preset?", preset.name)) return;
            await sched("/presets", {operation: "delete", name: preset.name});
            await refresh();
        }));
        const wrap = node("span", undefined, "shortcut-item");
        wrap.append(button, remove);
        $("presets").append(wrap);
    }
}

function renderSchedules() {
    const jobs = [...scheduler.jobs].sort((a, b) => new Date(a.at) - new Date(b.at));
    $("armed-state").textContent = scheduler.error ? "Locked" : scheduler.armed ? "Enabled" : "Paused";
    $("job-count").textContent = `${jobs.filter(job => job.enabled).length} active / ${jobs.length} saved schedules`;
    $("arm-toggle").textContent = scheduler.armed ? "Pause all scheduling" : "Enable scheduling";
    $("arm-toggle").className = scheduler.armed ? "danger" : "primary";
    $("arm-toggle").disabled = Boolean(scheduler.error);
    $("schedule-form").querySelector('[type="submit"]').disabled = Boolean(scheduler.error);
    hostname = scheduler.target || hostname;
    $("jobs").replaceChildren();
    if (!jobs.length) $("jobs").append(node("p", "Your queue is clear. Add a power schedule to get started.", "empty"));
    const actions = {on: "Power on · ATX", off: "Shut down · ATX", wol: "Wake-on-LAN", recover: "Guided recovery"};
    const repeat = {once: "One time", daily: "Every 24 hours", weekly: "Every 7 days", calendar: "Custom weekdays", weekdays: "Weekdays", weekends: "Weekends"};
    for (const job of jobs) {
        const row = node("article", undefined, "job");
        const heading = node("div", undefined, "panel-heading");
        heading.append(node("h4", job.name), node("span", job.enabled ? "QUEUED" : "INACTIVE", "pill"));
        row.append(heading, node("p", `${actions[job.action] || job.action} · ${repeat[job.repeat] || job.repeat}`), node("p", formatDate(job.at)));
        if (job.wall_time) row.append(node("p", `${job.timezone || "UTC"} ${job.wall_time}`));
        if (job.mac) row.append(node("p", job.mac));
        const controls = node("div", undefined, "job-actions");
        for (const operation of [job.enabled ? "pause" : "resume", "snooze", "skip", "delete"]) {
            const button = node("button", operation[0].toUpperCase() + operation.slice(1));
            button.disabled = Boolean(scheduler.error);
            button.addEventListener("click", () => perform(button, async () => {
                if (operation === "delete" && !await confirmAction("Delete schedule?", job.name)) return;
                const body = {id: job.id, operation};
                if (operation === "snooze") body.minutes = 15;
                await sched("/job", body);
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
    const settings = scheduler.settings || {};
    $("settings-form").elements.timezone.value = settings.timezone || "UTC";
    $("settings-form").elements.warning_seconds.value = settings.warning_seconds || 300;
    $("settings-form").elements.transition_timeout.value = settings.transition_timeout || 120;
    $("settings-form").elements.notify.checked = Boolean(settings.notifications?.enabled);
    if (!$("schedule-form").elements.timezone.value) $("schedule-form").elements.timezone.value = settings.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
    const active = scheduler.active;
    $("workflow").hidden = !active;
    if (active) {
        $("workflow-title").textContent = (active.phase || "WORKFLOW").replace(/_/g, " ").toUpperCase();
        $("workflow-detail").textContent = `${active.name || "Manual"} on ${active.target || hostname}${active.observed_power ? " · seen " + active.observed_power : ""}`;
    }
    renderPresets();
    tick();
}

async function refresh() {
    if (refreshing) return;
    refreshing = true;
    const started = performance.now();
    const fleetResult = await Promise.allSettled([api("scheduler/fleet")]);
    if (fleetResult[0].status === "fulfilled") {
        fleet = fleetResult[0].value.targets || [];
        if (!fleet.some(item => item.id === selectedId)) selectedId = fleet[0]?.id || "local";
    } else {
        fleet = [{id: "local", label: location.host, host: "", url: "/command/"}];
    }
    renderFleet();
    const results = await Promise.allSettled([
        sched(""),
        remoteSelected() ? sched("/status") : api("atx"),
    ]);
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
    const status = powerResult.status === "fulfilled" ? powerResult.value : null;
    const power = remoteSelected() ? (status?.power === "on" ? "On" : status?.power === "off" ? "Off" : "Unknown") : powerState(status);
    atx = remoteSelected() ? null : status;
    $("power-state").textContent = power;
    $("atx-detail").textContent = remoteSelected()
        ? (status?.atx ? "Remote ATX available" : "Remote ATX unavailable")
        : (atx?.enabled ? "ATX accessory available" : "ATX unavailable or not connected");
    const busy = Boolean(scheduler?.active);
    $("power-on").disabled = busy || power === "On" || power === "Unknown" || power === "Unavailable";
    $("power-off").disabled = busy || power === "Off" || power === "Unknown" || power === "Unavailable";
    $("power-recover").disabled = busy || power === "Unknown" || power === "Unavailable";
    const online = schedulerResult.status === "fulfilled";
    $("connection").textContent = online ? "● CONNECTED" : "● DEGRADED";
    $("connection").className = `pill ${online ? "online" : "offline"}`;
    diagnostics = {host: location.host, target: hostname, checked_at: new Date().toISOString(), api: online ? "Connected" : "Partial / unavailable", request_ms: Math.round(performance.now() - started), power,
        scheduler: scheduler ? (scheduler.error ? "Locked" : scheduler.armed ? "Enabled" : "Paused") : "Unavailable",
        clock_offset_seconds: scheduler ? Math.round(scheduler.server_time - Date.now() / 1000) : null};
    $("diagnostics").replaceChildren();
    for (const [key, value] of Object.entries({API: diagnostics.api, Target: selectedLabel(), "Refresh time": `${diagnostics.request_ms} ms`, "ATX power": power, Scheduler: diagnostics.scheduler, "Clock offset": diagnostics.clock_offset_seconds === null ? "Unknown" : `${diagnostics.clock_offset_seconds} seconds`})) {
        $("diagnostics").append(node("dt", key), node("dd", String(value)));
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

function updateScheduleForm() {
    const form = $("schedule-form");
    const wol = form.elements.action.value === "wol";
    const calendar = civilRepeat(form.elements.repeat.value);
    $("schedule-mac-label").hidden = !wol;
    form.elements.mac.required = wol;
    $("wall-label").hidden = !calendar;
    $("days-box").hidden = form.elements.repeat.value !== "calendar";
    $("repeat-hint").textContent = calendar
        ? "Civil-time jobs skip missing spring-forward times and use the first fall-back hour. Missed runs are skipped."
        : "once/daily/weekly use fixed UTC intervals, so local time shifts with daylight saving. Missed runs are skipped.";
}

$("timezone").textContent = Intl.DateTimeFormat().resolvedOptions().timeZone;
$("refresh").addEventListener("click", () => perform($("refresh"), refresh));
$("arm-toggle").addEventListener("click", () => perform($("arm-toggle"), async () => {
    if (!scheduler) return;
    const armed = !scheduler.armed;
    if (armed && !await confirmAction("Enable power scheduling?", `Saved active schedules will run on ${selectedLabel()}, even after you close the browser. Check the target, clock, and ATX wiring first.`)) return;
    await sched("/armed", {armed});
    notice(armed ? "Scheduling enabled." : "Scheduling paused. Any command already dispatched may still finish.");
    await refresh();
}));
$("cancel-workflow").addEventListener("click", () => perform($("cancel-workflow"), async () => {
    if (!await confirmAction("Cancel remaining workflow steps?", "A command already sent cannot be undone.")) return;
    const result = await sched("/cancel", {});
    notice(result.message || "Cancellation requested.");
    await refresh();
}));

const scheduleForm = $("schedule-form");
scheduleForm.elements.action.addEventListener("change", updateScheduleForm);
scheduleForm.elements.repeat.addEventListener("change", updateScheduleForm);
function setDelay(minutes) {
    const date = new Date(Date.now() + minutes * 60000);
    date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
    scheduleForm.elements.at.value = date.toISOString().slice(0, 16);
}
setDelay(60);
for (const button of document.querySelectorAll("[data-delay]")) button.addEventListener("click", () => setDelay(Number(button.dataset.delay)));
function scheduleBody() {
    const body = Object.fromEntries(new FormData(scheduleForm));
    const localDate = new Date(body.at);
    if (Number.isNaN(localDate.getTime())) throw new Error("Choose a valid date and time.");
    body.at = localDate.toISOString();
    body.exceptions = String(body.exceptions || "").split(/\s+/).map(item => item.trim()).filter(Boolean);
    if (body.repeat === "calendar") body.days = [...scheduleForm.querySelectorAll('[name="day"]:checked')].map(input => Number(input.value));
    delete body.day;
    if (!civilRepeat(body.repeat)) {
        delete body.wall_time;
        delete body.days;
    }
    if (body.action !== "wol") delete body.mac;
    return body;
}
scheduleForm.addEventListener("submit", event => {
    event.preventDefault();
    perform(event.submitter, async () => {
        const body = scheduleBody();
        if (!await confirmAction("Save power schedule?", `${body.name}: ${body.action} at ${formatDate(body.at)} on ${selectedLabel()}. ${scheduler?.armed ? "Scheduling is enabled; this job will execute at its due time." : "Scheduling is paused."}`)) return;
        await sched("/jobs", body);
        notice("Schedule saved on " + selectedLabel() + ".");
        await refresh();
    });
});
$("save-preset").addEventListener("click", () => perform($("save-preset"), async () => {
    const body = scheduleBody();
    if (!await confirmAction("Save maintenance preset?", body.name)) return;
    await sched("/presets", body);
    notice("Preset saved.");
    await refresh();
}));

async function tracked(action, title, detail) {
    if (!await confirmAction(title, `${detail}\nHostname must match: ${hostname}`)) return;
    if (!hostname) throw new Error("Target hostname is unknown; refresh first.");
    await sched("/run", {action, confirm_target: hostname, mac: action === "wol" ? $("wake-form").elements.mac.value.trim() : undefined});
    notice("Workflow accepted. Watch the banner for confirmed power state.");
    await refresh();
}
$("power-on").addEventListener("click", () => perform($("power-on"), () => tracked("on", "Power on attached host?", "Send a tracked ATX power-on request to the computer connected to the selected KVM.")));
$("power-off").addEventListener("click", () => perform($("power-off"), () => tracked("off", "Shut down attached host?", "Send a tracked graceful ATX shutdown request. Save your work first; the OS decides how to handle the power button.")));
$("power-recover").addEventListener("click", () => perform($("power-recover"), () => tracked("recover", "Start guided recovery?", "Graceful off, confirm off, settle, then power on. OS health is not inferred.")));

$("wake-devices").addEventListener("change", () => { $("wake-form").elements.mac.value = $("wake-devices").value; });
$("wake-form").addEventListener("submit", event => {
    event.preventDefault();
    perform(event.submitter, () => tracked("wol", "Wake LAN device?", `Send a magic packet to ${$("wake-form").elements.mac.value.trim()} from ${selectedLabel()}.`));
});

function renderShortcuts() {
    $("shortcuts").replaceChildren();
    [...defaults, ...customShortcuts].forEach((shortcut, index) => {
        const item = node("span", undefined, "shortcut-item");
        const button = node("button", shortcut.name); button.title = shortcut.keys;
        button.disabled = remoteSelected();
        button.addEventListener("click", () => perform(button, async () => {
            if (remoteSelected()) throw new Error("Keyboard shortcuts stay on this KVM. Open the selected target console.");
            if (!await confirmAction("Send keyboard shortcut?", `${shortcut.name} (${shortcut.keys}) will be sent to the computer attached to this KVM.`)) return;
            await api("hid/events/send_shortcut", undefined, {keys: shortcut.keys});
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
$("diagnostics-export").addEventListener("click", () => download("kvm-status.json", JSON.stringify(diagnostics, null, 2)));
$("backup-download").addEventListener("click", () => perform($("backup-download"), async () => {
    const data = await sched("/backup");
    download("kvmcontrol-backup.json", JSON.stringify(data, null, 2));
}));
$("restore-preview").addEventListener("click", () => perform($("restore-preview"), async () => {
    const file = $("backup-file").files[0];
    if (!file) throw new Error("Choose a backup JSON file first.");
    const backup = JSON.parse(await file.text());
    const mode = $("restore-mode").value;
    const preview = await sched("/restore", {backup, mode, phase: "preview"});
    restoring = {backup, mode, token: preview.token};
    $("restore-preview-out").hidden = false;
    $("restore-preview-out").textContent = `${preview.message}\nJobs: ${preview.jobs.length}\nPresets: ${preview.preset_count}\nMode: ${preview.mode}`;
    $("restore-commit").hidden = false;
}));
$("restore-commit").addEventListener("click", () => perform($("restore-commit"), async () => {
    if (!restoring) throw new Error("Preview a backup first.");
    if (!await confirmAction("Commit restore?", `Import ${restoring.mode} on ${selectedLabel()}. Jobs stay inactive. Pause scheduling first.`)) return;
    await sched("/restore", {...restoring, phase: "commit"});
    restoring = null;
    $("restore-commit").hidden = true;
    notice("Restore committed. Review inactive jobs before arming.");
    await refresh();
}));
$("settings-form").addEventListener("submit", event => {
    event.preventDefault();
    perform(event.submitter, async () => {
        const form = event.target;
        const body = {
            timezone: form.elements.timezone.value,
            warning_seconds: Number(form.elements.warning_seconds.value),
            transition_timeout: Number(form.elements.transition_timeout.value),
            notifications: {enabled: form.elements.notify.checked},
        };
        if (form.elements.url.value) body.notifications.url = form.elements.url.value;
        if (!await confirmAction("Save target settings?", "No test notification will be sent.")) return;
        await sched("/settings", body);
        notice("Settings saved.");
        await refresh();
    });
});

const launchActions = [
    ["Launch live console", () => location.assign("/")],
    ["Switch fleet target", () => $("fleet").scrollIntoView()],
    ["Schedule power on / shutdown", () => $("automation").scrollIntoView()],
    ["Wake-on-LAN", () => $("wake-form").scrollIntoView()],
    ["Backup and restore", () => $("ops").scrollIntoView()],
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
updateScheduleForm();
refresh();
api("wol/list").then(data => {
    for (const device of data.devices || []) {
        const option = node("option", `${device.name || device.mac} · ${device.mac}`); option.value = device.mac;
        $("wake-devices").append(option);
    }
}).catch(() => { $("wake-devices").replaceChildren(node("option", "Saved devices unavailable · enter MAC manually")); });
