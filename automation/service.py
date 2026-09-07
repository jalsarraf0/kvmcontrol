#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Additive Comet service: authenticated nginx -> root-only Unix socket.

Uses aiohttp and KVMD libraries already shipped by Comet firmware. Does not
replace, monkey-patch, restart, or import the KVMD server application.
"""
import asyncio
import fcntl
import logging
from logging.handlers import SysLogHandler
import os
from pathlib import Path

from aiohttp import web
from kvmd.htserver import HttpError, _get_exposed_http, make_json_exception
from kvmd.plugins.atx import get_atx_class

from automation.scheduler import SchedulerApi
from automation.fleet_api import FleetApi


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except HttpError as error:
        return make_json_exception(error)
    except web.HTTPException:
        raise
    except Exception:
        logging.exception("Automation request failed")
        return web.json_response({"ok": False, "result": {"error_msg": "Automation request failed; inspect service logs"}}, status=500)


async def lifecycle(app):
    scheduler = app["scheduler"]
    scheduler.sysprep()
    task = asyncio.create_task(scheduler.systask())
    notifications = asyncio.create_task(scheduler.notify_task())
    try:
        yield
    finally:
        await scheduler.cleanup()
        notifications.cancel()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await notifications
        except asyncio.CancelledError:
            pass


def main():
    logging.basicConfig(level=logging.INFO, handlers=[SysLogHandler(address="/dev/log")])
    os.umask(0o077)
    run = Path("/run/kvmcontrol")
    run.mkdir(mode=0o700, exist_ok=True)
    # Retained for process lifetime; prevents two services racing the store.
    with (run / "service.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        socket_path = run / "automation.sock"
        socket_path.unlink(missing_ok=True)
        scheduler = SchedulerApi(get_atx_class("glatx")())
        app = web.Application(middlewares=[errors], client_max_size=131072)
        app["scheduler"] = scheduler
        app.cleanup_ctx.append(lifecycle)
        for endpoint in [*_get_exposed_http(scheduler), *_get_exposed_http(FleetApi())]:
            app.router.add_route(endpoint.method, endpoint.path, endpoint.handler)
        web.run_app(app, path=str(socket_path), print=None, access_log=None)


if __name__ == "__main__":
    main()
