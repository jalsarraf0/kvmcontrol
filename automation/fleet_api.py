# SPDX-License-Identifier: GPL-3.0-or-later
from kvmd import aiotools
from kvmd.htserver import exposed_http, make_json_response, BadRequestError
from automation import fleet


class FleetApi:
    @exposed_http('GET', '/scheduler/fleet')
    async def overview(self, _):
        return make_json_response({'targets': await aiotools.run_async(fleet.overview)})

    @exposed_http('POST', '/scheduler/fleet')
    async def command(self, req):
        try:
            data = await req.json()
            if not isinstance(data, dict):
                raise ValueError('Expected an object')
            result = await aiotools.run_async(fleet.call, data.get('target'), data.get('path', '/scheduler'), data.get('body'))
            return make_json_response(result)
        except (ValueError, RuntimeError) as error:
            raise BadRequestError(str(error)) from error
