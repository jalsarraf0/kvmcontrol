# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable, opt-in host power jobs. Never replay an uncertain dispatch."""

import asyncio
import copy
import datetime
import json
import hashlib
import os
import re
import socket
import tempfile
import time
import uuid

from aiohttp.web import Request, Response
from aiohttp import ClientSession, ClientTimeout
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from automation import calendar_rules as calendar

from kvmd import aiotools
from kvmd.htserver import BadRequestError, HttpError, exposed_http, make_json_response
from kvmd.logging import get_logger
from kvmd.plugins.atx import BaseAtx


class SchedulerApi:
    """Single KVMD process owns the store; all changes serialize with dispatch."""

    def __init__(self, atx: BaseAtx, path: str = "/etc/kvmd/user/power-schedules.json") -> None:
        self.__atx = atx
        self.__path = path
        self.__lock = asyncio.Lock()
        self.__data: dict = {"version": 2, "armed": False, "jobs": [], "history": [], "presets": [], "settings": self.__default_settings()}
        self.__error = ""
        self.__active = None
        self.__execution = None
        self.__cancel = asyncio.Event()
        self.__hardware = None
        self.__warning_keys = set()
        self.__notices = asyncio.Queue(maxsize=20)

    @staticmethod
    def __default_settings():
        return {"timezone": "UTC", "warning_seconds": 300, "transition_timeout": 120,
                "notifications": {"enabled": False, "url": ""}}


    def sysprep(self) -> None:
        try:
            with open(self.__path, encoding="utf-8") as file:
                data = json.load(file)
            if (data.get("version") not in (1, 2) or type(data.get("armed")) is not bool
                    or not isinstance(data.get("jobs"), list) or len(data["jobs"]) > 64
                    or not isinstance(data.get("history"), list) or len(data["history"]) > 100):
                raise ValueError("Invalid scheduler store")
            data.setdefault("presets", [])
            data["settings"] = self.__settings(data.get("settings", self.__default_settings()))
            if not isinstance(data["presets"], list) or len(data["presets"]) > 16:
                raise ValueError("Invalid presets")
            data["presets"] = [self.__validate_job(preset, future=False) for preset in data["presets"]]
            ids = set()
            for job in data["jobs"]:
                job.update(self.__validate_job(job, future=False))
                if (not isinstance(job.get("id"), str) or job["id"] in ids
                        or type(job.get("enabled")) is not bool):
                    raise ValueError("Invalid job identity or state")
                ids.add(job["id"])
            changed = data["version"] != 2
            data["version"] = 2
            for job in data["jobs"]:
                if job["enabled"] and self.__due(job) <= time.time():
                    self.__advance(job, time.time())
                    self.__event(data, job, "skipped", "Due before service startup; never replayed")
                    changed = True
            if changed:
                self.__write(data)
            self.__data = data
        except FileNotFoundError:
            pass
        except Exception:
            self.__error = "Cannot read scheduler store; scheduling is locked until the file is repaired and the kvmcontrol service is restarted."
            get_logger(0).exception("Power scheduler store is invalid")

    @staticmethod
    def __validate_job(body: dict, future: bool = True) -> dict:
        if not isinstance(body, dict):
            raise BadRequestError("Expected a JSON object")
        name = body.get("name", "")
        action = body.get("action")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise BadRequestError("Name must contain 1–80 characters")
        if action not in ("on", "off", "wol", "recover"):
            raise BadRequestError("Action must be on, off, wol, or recover")
        try:
            due = datetime.datetime.fromisoformat(body["at"].replace("Z", "+00:00"))
            if due.tzinfo is None:
                raise ValueError()
            timestamp = due.timestamp()
        except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as ex:
            raise BadRequestError("Time must be ISO 8601 with a UTC offset") from ex
        if future and not time.time() + 10 <= timestamp <= time.time() + 366 * 86400:
            raise BadRequestError("Choose a time between 10 seconds and 366 days from now")
        repeat = body.get("repeat", "once")
        if repeat not in ("once", "daily", "weekly", "calendar", "weekdays", "weekends"):
            raise BadRequestError("Invalid recurrence rule")
        mac = body.get("mac", "")
        if not isinstance(mac, str) or (action == "wol" and not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac)):
            raise BadRequestError("Wake-on-LAN requires a colon-separated MAC address")
        if action == "wol" and (int(mac[:2], 16) & 1 or mac.lower() == "00:00:00:00:00:00"):
            raise BadRequestError("Use the target network adapter's unicast MAC address")
        try:
            rule = calendar.fields(body, timestamp, repeat)
            job = {"name": name.strip(), "action": action, "at": calendar.iso(timestamp),
                   "repeat": repeat, "mac": mac.lower() if action == "wol" else "", **rule}
            anchor = body.get("anchor", job["at"])
            calendar.stamp(anchor)
            job["anchor"] = anchor
            if future and repeat in calendar.CALENDAR:
                job["at"] = calendar.iso(calendar.calendar_next(job, timestamp - 1))
            return job
        except (ValueError, TypeError, KeyError) as error:
            raise BadRequestError(str(error)) from error

    def __write(self, data: dict) -> None:
        directory = os.path.dirname(self.__path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".power-schedules-", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.__path)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def __save(self, data: dict) -> None:
        if self.__error:
            raise HttpError(self.__error, 503)
        try:
            await aiotools.run_async(self.__write, data)
        except Exception as ex:
            self.__error = "Scheduler storage failed; dispatch is locked until the kvmcontrol service restarts."
            get_logger(0).exception("Cannot persist power schedules")
            raise HttpError(self.__error, 503) from ex
        self.__data = data

    @staticmethod
    def __event(data: dict, job: dict, status: str, detail: str) -> None:
        data["history"].insert(0, {"time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                   "job_id": job.get("id", ""), "name": job.get("name", "Scheduler"),
                                   "status": status, "detail": detail})
        del data["history"][100:]

    @staticmethod
    async def __body(req: Request) -> dict:
        try:
            body = await req.json()
        except (ValueError, UnicodeError) as ex:
            raise BadRequestError("Expected JSON") from ex
        if not isinstance(body, dict):
            raise BadRequestError("Expected a JSON object")
        return body

    @exposed_http("GET", "/scheduler")
    async def __state(self, _: Request) -> Response:
        async with self.__lock:
            data = copy.deepcopy(self.__data)
            data["settings"]["notifications"] = {"enabled": data["settings"]["notifications"]["enabled"],
                                                    "configured": bool(data["settings"]["notifications"]["url"])}
            data.update({"error": self.__error, "server_time": time.time(), "grace_seconds": 60,
                         "active": copy.deepcopy(self.__active), "target": socket.gethostname()})
        return make_json_response(data)

    @exposed_http("POST", "/scheduler/armed")
    @aiotools.atomic_fg
    async def __arm(self, req: Request) -> Response:
        armed = (await self.__body(req)).get("armed")
        if type(armed) is not bool:
            raise BadRequestError("armed must be a boolean")
        async with self.__lock:
            data = copy.deepcopy(self.__data)
            # Arming never replays anything due while paused.
            if armed:
                for job in data["jobs"]:
                    if job["enabled"] and self.__due(job) <= time.time():
                        self.__advance(job, time.time())
                        self.__event(data, job, "skipped", "Due while scheduler was paused")
            data["armed"] = armed
            if not armed:
                self.__cancel.set()
            self.__event(data, {}, "armed" if armed else "paused", "Scheduler setting changed")
            await self.__save(data)
        return make_json_response()

    @exposed_http("POST", "/scheduler/jobs")
    @aiotools.atomic_fg
    async def __create(self, req: Request) -> Response:
        job = self.__validate_job(await self.__body(req))
        async with self.__lock:
            if len(self.__data["jobs"]) >= 64:
                raise BadRequestError("Limit of 64 jobs; delete completed jobs first")
            data = copy.deepcopy(self.__data)
            job.update({"id": uuid.uuid4().hex, "enabled": True})
            data["jobs"].append(job)
            self.__event(data, job, "created", "Schedule saved")
            await self.__save(data)
        return make_json_response(job)

    @exposed_http("POST", "/scheduler/job")
    @aiotools.atomic_fg
    async def __change(self, req: Request) -> Response:
        body = await self.__body(req)
        operation = body.get("operation")
        if operation not in ("pause", "resume", "delete", "snooze", "skip"):
            raise BadRequestError("Operation must be pause, resume, delete, snooze, or skip")
        async with self.__lock:
            data = copy.deepcopy(self.__data)
            job = next((item for item in data["jobs"] if item["id"] == body.get("id")), None)
            if job is None:
                raise HttpError("Schedule not found", 404)
            if self.__active and self.__active["id"] == job["id"]:
                raise HttpError("This job is already running; cancel the active workflow instead", 409)
            if operation == "snooze":
                minutes = body.get("minutes", 15)
                if type(minutes) is not int or not 1 <= minutes <= 10080:
                    raise BadRequestError("Snooze must be 1–10080 minutes")
                job["at"] = calendar.iso(max(time.time(), self.__due(job)) + minutes * 60)
                job["enabled"] = True
            elif operation == "skip":
                self.__advance(job, max(time.time(), self.__due(job)))
            elif operation == "delete":
                data["jobs"].remove(job)
            else:
                if operation == "resume" and self.__due(job) <= time.time():
                    if job["repeat"] == "once":
                        raise BadRequestError("Past one-time job: create a new schedule")
                    self.__advance(job, time.time())
                job["enabled"] = operation == "resume"
            self.__event(data, job, operation, "Schedule updated")
            await self.__save(data)
        return make_json_response()

    @staticmethod
    def __due(job: dict) -> float:
        return datetime.datetime.fromisoformat(job["at"].replace("Z", "+00:00")).timestamp()

    @staticmethod
    def __advance(job: dict, now: float) -> None:
        calendar.advance(job, now)

    @staticmethod
    def __wake(mac: str) -> None:
        packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(3)
            sock.sendto(packet, ("255.255.255.255", 9))

    @staticmethod
    def __settings(body):
        if not isinstance(body, dict):
            raise BadRequestError("Expected settings object")
        settings = SchedulerApi.__default_settings()
        for key in ("timezone", "warning_seconds", "transition_timeout", "notifications"):
            if key in body:
                settings[key] = body[key]
        if not isinstance(settings['timezone'], str) or not settings['timezone'] or len(settings['timezone']) > 80:
            raise BadRequestError("Unknown IANA time zone")
        try:
            ZoneInfo(settings['timezone'])
        except (ValueError, TypeError, KeyError, OSError) as error:
            raise BadRequestError("Unknown IANA time zone") from error
        for key, low, high in (("warning_seconds", 30, 3600), ("transition_timeout", 15, 600)):
            if type(settings[key]) is not int or not low <= settings[key] <= high:
                raise BadRequestError(f"{key} must be between {low} and {high}")
        notices = settings['notifications']
        if not isinstance(notices, dict) or type(notices.get('enabled')) is not bool:
            raise BadRequestError("Invalid notification settings")
        url = notices.get('url', '')
        if not isinstance(url, str) or len(url) > 2048:
            raise BadRequestError("Invalid webhook URL")
        try:
            parsed = urlsplit(url)
            if url and (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment):
                raise ValueError()
            if notices['enabled'] and not url:
                raise ValueError()
        except ValueError as error:
            raise BadRequestError("Notifications require an HTTPS webhook URL without embedded credentials") from error
        settings['notifications'] = {'enabled': notices['enabled'], 'url': url}
        return settings

    @exposed_http("POST", "/scheduler/settings")
    @aiotools.atomic_fg
    async def __configure(self, req):
        body = await self.__body(req)
        async with self.__lock:
            data = copy.deepcopy(self.__data)
            merged = {**data['settings'], **body}
            if isinstance(body.get('notifications'), dict) and 'url' not in body['notifications']:
                merged['notifications'] = {**data['settings']['notifications'], **body['notifications']}
            data['settings'] = self.__settings(merged)
            self.__event(data, {}, 'settings', 'Settings updated; no test notification sent')
            await self.__save(data)
        return make_json_response()

    @exposed_http("POST", "/scheduler/presets")
    @aiotools.atomic_fg
    async def __preset(self, req):
        body = await self.__body(req)
        async with self.__lock:
            data = copy.deepcopy(self.__data)
            if body.get('operation') == 'delete':
                name = body.get('name')
                if not isinstance(name, str) or not name.strip():
                    raise BadRequestError('Preset name required')
                data['presets'] = [item for item in data['presets'] if item['name'] != name]
            else:
                preset = self.__validate_job(body, future=False)
                data['presets'] = [item for item in data['presets'] if item['name'] != preset['name']]
                if len(data['presets']) >= 16:
                    raise BadRequestError('Maximum 16 presets')
                data['presets'].append(preset)
            await self.__save(data)
        return make_json_response()

    @exposed_http("GET", "/scheduler/backup")
    async def __backup(self, _):
        async with self.__lock:
            data = copy.deepcopy(self.__data)
            data.pop('armed')
            data['format'] = 'kvmcontrol-backup-v2'
            data['settings']['notifications'] = {'enabled': False, 'url': ''}
        return make_json_response(data)

    def __restore_plan(self, body):
        backup = body.get('backup')
        if not isinstance(backup, dict) or backup.get('format') != 'kvmcontrol-backup-v2':
            raise BadRequestError('Choose a kvmcontrol v2 backup')
        incoming = backup.get('jobs')
        presets = backup.get('presets', [])
        if not isinstance(incoming, list) or len(incoming) > 64 or not isinstance(presets, list) or len(presets) > 16:
            raise BadRequestError('Invalid backup limits')
        jobs = [self.__validate_job(job, future=False) for job in incoming]
        presets = [self.__validate_job(item, future=False) for item in presets]
        settings = self.__settings(backup.get('settings', {}))
        mode = body.get('mode', 'merge')
        if mode not in ('merge', 'replace'):
            raise BadRequestError('Restore mode must be merge or replace')
        total = len(jobs) + (len(self.__data['jobs']) if mode == 'merge' else 0)
        if total > 64:
            raise BadRequestError('Restored queue would exceed 64 jobs')
        digest = hashlib.sha256(json.dumps([self.__data, backup, mode], sort_keys=True).encode()).hexdigest()
        return jobs, presets, settings, mode, digest

    @exposed_http("POST", "/scheduler/restore")
    @aiotools.atomic_fg
    async def __restore(self, req):
        body = await self.__body(req)
        async with self.__lock:
            jobs, presets, settings, mode, digest = self.__restore_plan(body)
            if body.get('phase', 'preview') == 'preview':
                return make_json_response({'token': digest, 'jobs': jobs, 'preset_count': len(presets), 'mode': mode,
                                           'message': 'Imported jobs will be inactive; scheduling must be paused. Webhook credentials and history are not restored.'})
            if body.get('phase') != 'commit' or body.get('token') != digest:
                raise HttpError('Preview expired or invalid; preview again before restoring', 409)
            if self.__data['armed'] or self.__active:
                raise HttpError('Pause scheduling and wait for the active workflow to finish before restoring', 409)
            data = copy.deepcopy(self.__data)
            data['jobs'] = ([] if mode == 'replace' else data['jobs']) + [dict(job, id=uuid.uuid4().hex, enabled=False) for job in jobs]
            if mode == 'replace':
                data['presets'] = presets
            else:
                incoming = {item['name'] for item in presets}
                data['presets'] = [item for item in data['presets'] if item['name'] not in incoming] + presets
                if len(data['presets']) > 16:
                    raise BadRequestError('Restored presets would exceed 16')
            settings['notifications'] = data['settings']['notifications']
            data['settings'] = settings
            self.__event(data, {}, 'restored', f'{len(jobs)} inactive jobs restored ({mode})')
            await self.__save(data)
        return make_json_response()

    def __busy(self):
        return bool(self.__active) or (self.__hardware is not None and not self.__hardware.done())

    @exposed_http("GET", "/scheduler/status")
    async def __status(self, _):
        state = await self.__atx.get_state()
        async with self.__lock:
            active = copy.deepcopy(self.__active)
            armed = self.__data['armed']
            error = self.__error
            jobs = len(self.__data['jobs'])
        return make_json_response({'target': socket.gethostname(), 'power': self.__power(state),
                                   'atx': bool(state.get('enabled')), 'active': active,
                                   'armed': armed, 'error': error, 'jobs': jobs})

    @exposed_http("POST", "/scheduler/run")
    @aiotools.atomic_fg
    async def __run(self, req):
        body = await self.__body(req)
        if body.get('confirm_target') != socket.gethostname():
            raise BadRequestError('Confirm the exact target hostname')
        body.update({'name': 'Manual ' + str(body.get('action', 'operation')), 'at': calendar.iso(time.time() + 30), 'repeat': 'once'})
        job = self.__validate_job(body)
        job.update(id=uuid.uuid4().hex, enabled=False)
        async with self.__lock:
            if self.__busy():
                raise HttpError('Another power workflow is active', 409)
            data = copy.deepcopy(self.__data)
            self.__event(data, job, 'dispatching', 'Manual workflow accepted and recorded')
            await self.__save(data)
            self.__start(job)
        return make_json_response({'id': job['id'], 'accepted': True})

    @exposed_http("POST", "/scheduler/cancel")
    async def __cancel_handler(self, _):
        async with self.__lock:
            self.__cancel.set()
            if self.__active:
                self.__active['phase'] = 'cancelling'
        return make_json_response({'message': 'Cancellation requested; commands already sent cannot be undone'})

    @staticmethod
    def __power(state):
        if not state.get('enabled'):
            return 'unknown'
        value = state.get('power', state.get('leds', {}).get('power'))
        return 'on' if value is True or value == 'on' else 'off' if value is False or value == 'off' else 'unknown'

    async def __phase(self, phase):
        async with self.__lock:
            if self.__cancel.is_set():
                raise InterruptedError('Cancelled; previously dispatched commands may still finish')
            self.__active['phase'] = phase

    async def __observe(self, desired):
        deadline = time.monotonic() + self.__data['settings']['transition_timeout']
        stable = 0
        await self.__phase('waiting_for_' + desired)
        while time.monotonic() < deadline:
            if self.__cancel.is_set():
                raise InterruptedError('Cancelled during power observation')
            state = await asyncio.wait_for(self.__atx.get_state(), 8)
            power = self.__power(state)
            async with self.__lock:
                if self.__active:
                    self.__active['observed_power'] = power
            stable = stable + 1 if power == desired else 0
            if stable >= 2:
                return
            await asyncio.sleep(2)
        raise TimeoutError('Power transition was not confirmed before timeout; no escalation or retry')

    async def __command(self, action):
        # Cancellation and dispatch admission share a lock; after this point a command may finish.
        async with self.__lock:
            if self.__cancel.is_set():
                raise InterruptedError('Workflow cancelled before command dispatch')
            data = copy.deepcopy(self.__data)
            self.__event(data, self.__active, 'command', 'Sending ATX ' + action)
            await self.__save(data)
            self.__active['phase'] = 'sending_' + action
            self.__hardware = asyncio.create_task(
                (self.__atx.power_on if action == 'on' else self.__atx.power_off)(True)
            )
            command = self.__hardware
        try:
            await asyncio.wait_for(asyncio.shield(command), 10)
        except asyncio.TimeoutError as error:
            raise TimeoutError('ATX command did not finish within 10s; the hardware request may still be in progress') from error
        finally:
            if self.__hardware is not None and self.__hardware.done():
                failure = None if self.__hardware.cancelled() else self.__hardware.exception()
                self.__hardware = None
                if failure:
                    raise failure

    async def __dispatch(self, job):
        await self.__phase('checking_target')
        if job['action'] == 'wol':
            if self.__cancel.is_set():
                raise InterruptedError('Cancelled before wake packet')
            await aiotools.run_async(self.__wake, job['mac'])
            return 'Wake packet sent; host startup cannot be confirmed through WOL'
        state = await asyncio.wait_for(self.__atx.get_state(), 8)
        power = self.__power(state)
        if not state.get('enabled') or state.get('busy') or power == 'unknown':
            raise RuntimeError('ATX unavailable, busy, or unknown; no command sent')
        if job['action'] == 'recover':
            if power == 'on':
                await self.__command('off')
            await self.__observe('off')
            await self.__phase('off_settle')
            await asyncio.sleep(3)
            if self.__power(await self.__atx.get_state()) != 'off':
                raise RuntimeError('Host did not remain off; recovery stopped')
            await self.__command('on')
            await self.__observe('on')
            return 'Recovery completed: power-off and power-on confirmed; OS health is not inferred'
        desired = job['action']
        if power != desired:
            await self.__command(desired)
        await self.__observe(desired)
        return 'ATX power state confirmed ' + desired + '; OS health is not inferred'

    def __start(self, job):
        self.__cancel.clear()
        self.__active = {**job, 'phase': 'starting', 'started': time.time(), 'target': socket.gethostname()}
        self.__execution = asyncio.create_task(self.__execute(job))

    async def __record(self, job, status, detail):
        async with self.__lock:
            data = copy.deepcopy(self.__data)
            self.__event(data, job, status, detail)
            await self.__save(data)
            self.__enqueue_notice(data['history'][0])

    async def __execute(self, job):
        status, detail = 'failed', 'Unknown failure'
        try:
            detail = await self.__dispatch(job)
            status = 'completed'
        except InterruptedError as error:
            status, detail = 'cancelled', str(error)
        except TimeoutError as error:
            status, detail = 'timeout', str(error)
        except asyncio.CancelledError:
            status, detail = 'interrupted', 'Service stopping; a command already sent cannot be undone'
            try:
                await self.__record(job, status, detail)
            except Exception:
                get_logger(0).exception('Cannot persist interrupted workflow')
            finally:
                self.__active = None
            raise
        except Exception as error:
            get_logger(0).exception('Power workflow failed')
            status, detail = 'failed', str(error)[:300]
        try:
            await self.__record(job, status, detail)
        except Exception:
            get_logger(0).exception('Cannot persist workflow result')
        finally:
            self.__active = None

    def __enqueue_notice(self, event):
        config = self.__data['settings']['notifications']
        if not config['enabled']:
            return
        payload = {key: event.get(key, '') for key in ('time', 'job_id', 'name', 'status', 'detail')}
        try:
            self.__notices.put_nowait((config['url'], payload))
        except asyncio.QueueFull:
            get_logger(0).warning('Notification queue full; notification dropped')

    async def notify_task(self):
        while True:
            url, event = await self.__notices.get()
            status = 'notification_sent'
            try:
                # Generic JSON webhook; never include notes, configuration, credentials or MACs.
                async with ClientSession(timeout=ClientTimeout(total=8)) as session:
                    async with session.post(url, json={'source': 'kvmcontrol', 'target': socket.gethostname(), 'event': event}, allow_redirects=False) as response:
                        if not 200 <= response.status < 300:
                            raise RuntimeError('Webhook rejected notification')
            except asyncio.CancelledError:
                raise
            except Exception:
                status = 'notification_failed'
                get_logger(0).warning('Webhook delivery failed; URL and response omitted')
            try:
                async with self.__lock:
                    data = copy.deepcopy(self.__data)
                    self.__event(data, {}, status, 'Delivery attempted once; no retries')
                    await self.__save(data)
            except asyncio.CancelledError:
                raise
            except Exception:
                get_logger(0).exception('Cannot record notification result')
            finally:
                self.__notices.task_done()

    async def cleanup(self):
        self.__cancel.set()
        if self.__execution:
            self.__execution.cancel()
            try:
                await self.__execution
            except asyncio.CancelledError:
                pass
        if self.__hardware is not None:
            try:
                await self.__hardware
            except Exception:
                get_logger(0).exception('In-flight ATX command finished with an error during shutdown')
            self.__hardware = None

    async def systask(self):
        while True:
            try:
                async with self.__lock:
                    if self.__data['armed'] and not self.__error:
                        now = time.time()
                        if len(self.__warning_keys) > 256:
                            self.__warning_keys.clear()
                        for original in sorted(self.__data['jobs'], key=self.__due):
                            if not original['enabled']:
                                continue
                            due = self.__due(original)
                            warning_key = (original['id'], original['at'])
                            if 0 < due - now <= self.__data['settings']['warning_seconds'] and warning_key not in self.__warning_keys:
                                data = copy.deepcopy(self.__data)
                                self.__event(data, original, 'upcoming', 'Operation due at ' + original['at'])
                                await self.__save(data)
                                self.__warning_keys.add(warning_key)
                                self.__enqueue_notice(data['history'][0])
                            if due > now or self.__busy():
                                continue
                            data = copy.deepcopy(self.__data)
                            job = next(item for item in data['jobs'] if item['id'] == original['id'])
                            dispatched = copy.deepcopy(job)
                            late = now - due > 60
                            self.__advance(job, now)
                            self.__event(data, job, 'skipped' if late else 'dispatching',
                                         'Missed execution window' if late else 'Occurrence consumed before dispatch; no retries')
                            await self.__save(data)
                            if not late:
                                self.__start(dispatched)
                            break
            except Exception:
                get_logger(0).exception('Power scheduler iteration failed')
            await asyncio.sleep(1)
