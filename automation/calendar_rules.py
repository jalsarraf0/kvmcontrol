# SPDX-License-Identifier: GPL-3.0-or-later
"""Civil-time recurrence with explicit DST policy and bounded date exclusions."""
import datetime as dt
import math
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
CALENDAR = {'calendar', 'weekdays', 'weekends'}


def stamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Time requires a UTC offset')
    return parsed.timestamp()


def iso(value):
    return dt.datetime.fromtimestamp(value, UTC).isoformat()


def calendar_next(job, after):
    zone = ZoneInfo(job['timezone'])
    local = dt.datetime.fromtimestamp(after, zone)
    hour, minute = map(int, job['wall_time'].split(':'))
    days = job['days']
    excluded = set(job['exceptions'])
    for offset in range(740):
        date = local.date() + dt.timedelta(days=offset)
        if date.weekday() not in days or date.isoformat() in excluded:
            continue
        candidate = dt.datetime.combine(date, dt.time(hour, minute), zone).replace(fold=0)
        timestamp = candidate.timestamp()
        # Skip nonexistent spring-forward times; choose the FIRST fall-back occurrence.
        roundtrip = dt.datetime.fromtimestamp(timestamp, zone)
        if roundtrip.replace(tzinfo=None) != candidate.replace(tzinfo=None):
            continue
        if timestamp > after:
            return timestamp
    raise ValueError('No execution date available in the next two years')


def fields(body, at, repeat):
    zone_name = body.get('timezone', 'UTC')
    if not isinstance(zone_name, str) or not zone_name or len(zone_name) > 80:
        raise ValueError('Choose an IANA time zone')
    try:
        zone = ZoneInfo(zone_name)
    except Exception as error:
        raise ValueError('Choose an IANA time zone') from error
    default_days = list(range(5)) if repeat == 'weekdays' else [5, 6] if repeat == 'weekends' else list(range(7))
    days = body.get('days', default_days)
    if not isinstance(days, list) or not days or len(days) > 7 or any(type(day) is not int or day not in range(7) for day in days):
        raise ValueError('Days must be a list of weekday numbers, Monday=0 through Sunday=6')
    if repeat in ('weekdays', 'weekends'):
        days = default_days
    exceptions = body.get('exceptions', [])
    if not isinstance(exceptions, list) or len(exceptions) > 64:
        raise ValueError('Use at most 64 excluded dates')
    for date in exceptions:
        if not isinstance(date, str) or dt.date.fromisoformat(date).isoformat() != date:
            raise ValueError('Excluded dates must be YYYY-MM-DD')
    wall = body.get('wall_time', dt.datetime.fromtimestamp(at, zone).strftime('%H:%M'))
    if not isinstance(wall, str) or len(wall) != 5 or dt.time.fromisoformat(wall).strftime('%H:%M') != wall:
        raise ValueError('Local time must be HH:MM')
    return {'timezone': zone_name, 'wall_time': wall, 'days': sorted(set(days)), 'exceptions': sorted(set(exceptions))}


def advance(job, after):
    if job['repeat'] == 'once':
        job['enabled'] = False
    elif job['repeat'] in CALENDAR:
        job['at'] = iso(calendar_next(job, after))
    else:
        interval = 86400 if job['repeat'] == 'daily' else 604800
        anchor = stamp(job.get('anchor', job['at']))
        job['at'] = iso(anchor + (math.floor((after - anchor) / interval) + 1) * interval)
