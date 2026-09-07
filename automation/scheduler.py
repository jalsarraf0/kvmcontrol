# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable, opt-in host power jobs. Never replay an uncertain dispatch."""

import asyncio
import copy
import datetime
import json
import math
import os
import re
import socket
import tempfile
import time
import uuid

from aiohttp.web import Request, Response

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
        self.__data: dict = {"version": 1, "armed": False, "jobs": [], "history": []}
        self.__error = ""

    def sysprep(self) -> None:
        try:
            with open(self.__path, encoding="utf-8") as file:
                data = json.load(file)
            if (data.get("version") != 1 or type(data.get("armed")) is not bool
                    or not isinstance(data.get("jobs"), list) or len(data["jobs"]) > 64
                    or not isinstance(data.get("history"), list) or len(data["history"]) > 100):
                raise ValueError("Invalid scheduler store")
            ids = set()
            for job in data["jobs"]:
                self.__validate_job(job, future=False)
                if (not isinstance(job.get("id"), str) or job["id"] in ids
                        or type(job.get("enabled")) is not bool):
                    raise ValueError("Invalid job identity or state")
                ids.add(job["id"])
            changed = False
            for job in data["jobs"]:
                if job["enabled"] and self.__due(job) <= time.time():
                    self.__advance(job, time.time())
                    self.__event(data, job, "skipped", "Due before daemon startup; never replayed")
                    changed = True
            if changed:
                self.__write(data)
            self.__data = data
        except FileNotFoundError:
            pass
        except Exception:
            self.__error = "Cannot read scheduler store; scheduling is locked until the file is repaired and KVMD restarted."
            get_logger(0).exception("Power scheduler store is invalid")

    @staticmethod
    def __validate_job(body: dict, future: bool = True) -> dict:
        if not isinstance(body, dict):
            raise BadRequestError("Expected a JSON object")
        name = body.get("name", "")
        action = body.get("action")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise BadRequestError("Name must contain 1–80 characters")
        if action not in ("on", "off", "wol"):
            raise BadRequestError("Action must be on, off, or wol")
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
        if repeat not in ("once", "daily", "weekly"):
            raise BadRequestError("Repeat must be once, daily, or weekly")
        mac = body.get("mac", "")
        if not isinstance(mac, str) or (action == "wol" and not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac)):
            raise BadRequestError("Wake-on-LAN requires a colon-separated MAC address")
        if action == "wol" and (int(mac[:2], 16) & 1 or mac.lower() == "00:00:00:00:00:00"):
            raise BadRequestError("Use the target network adapter's unicast MAC address")
        return {"name": name.strip(), "action": action, "at": due.astimezone(datetime.timezone.utc).isoformat(),
                "repeat": repeat, "mac": mac.lower() if action == "wol" else ""}

    def __write(self, data: dict) -> None:
        directory = os.path.dirname(self.__path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".power-schedules-", dir=directory)
        try:
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
            self.__error = "Scheduler storage failed; dispatch is locked until KVMD restarts."
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
            data.update({"error": self.__error, "server_time": time.time(), "grace_seconds": 60})
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
        if operation not in ("pause", "resume", "delete"):
            raise BadRequestError("Operation must be pause, resume, or delete")
        async with self.__lock:
            data = copy.deepcopy(self.__data)
            job = next((item for item in data["jobs"] if item["id"] == body.get("id")), None)
            if job is None:
                raise HttpError("Schedule not found", 404)
            if operation == "delete":
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

    @classmethod
    def __advance(cls, job: dict, now: float) -> None:
        if job["repeat"] == "once":
            job["enabled"] = False
        else:
            interval = 86400 if job["repeat"] == "daily" else 7 * 86400
            due = cls.__due(job)
            due += (math.floor((now - due) / interval) + 1) * interval
            job["at"] = datetime.datetime.fromtimestamp(due, datetime.timezone.utc).isoformat()

    @staticmethod
    def __wake(mac: str) -> None:
        packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(3)
            sock.sendto(packet, ("255.255.255.255", 9))

    async def __dispatch(self, job: dict) -> str:
        if job["action"] == "wol":
            await aiotools.run_async(self.__wake, job["mac"])
            return "Wake packet sent; host startup is not confirmed"
        state = await self.__atx.get_state()
        if not state.get("enabled") or state.get("busy"):
            raise RuntimeError("ATX unavailable or busy; no command sent")
        # GL-ATX reports a string; upstream GPIO plugins report the power LED.
        power = state.get("power")
        if power is None:
            power = state.get("leds", {}).get("power")
        if power in ("on", True):
            is_on = True
        elif power in ("off", False):
            is_on = False
        else:
            raise RuntimeError("ATX power state is unknown; no command sent")
        if is_on == (job["action"] == "on"):
            return "Already in requested power state; no command sent"
        await (self.__atx.power_on if job["action"] == "on" else self.__atx.power_off)(True)
        return "ATX command completed; host transition is not confirmed"

    async def systask(self) -> None:
        while True:
            try:
                async with self.__lock:
                    if self.__data["armed"] and not self.__error:
                        for original in self.__data["jobs"]:
                            now = time.time()
                            if not original["enabled"] or self.__due(original) > now:
                                continue
                            data = copy.deepcopy(self.__data)
                            job = next(item for item in data["jobs"] if item["id"] == original["id"])
                            late = now - self.__due(job) > 60
                            self.__advance(job, now)
                            self.__event(data, job, "skipped" if late else "dispatching",
                                         "Missed execution window" if late else "Claim persisted; interrupted dispatches are never retried")
                            # Consume the occurrence durably BEFORE touching any hardware.
                            await self.__save(data)
                            if not late:
                                try:
                                    detail = await asyncio.wait_for(self.__dispatch(job), timeout=20)
                                    status = "completed"
                                except Exception:
                                    get_logger(0).exception("Scheduled power operation failed")
                                    detail = "Failed or timed out; inspect host state before scheduling again"
                                    status = "failed"
                                data = copy.deepcopy(self.__data)
                                self.__event(data, job, status, detail)
                                await self.__save(data)
                            break
            except Exception:
                get_logger(0).exception("Power scheduler iteration failed")
            await asyncio.sleep(1)
