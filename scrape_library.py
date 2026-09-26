"""
Scrape UCLA Library hours and group study room availability from the library's
LibCal site (calendar.library.ucla.edu) into library.json.

Two public feeds, no login:

- Hours: api_hours_grid.php lists every library and department (Powell, YRL,
  Night Powell, CLICC lab, Biomedical Study Commons, ...) with open hours per
  date for the next couple of weeks. spaces.json entries carry an `hours_lid`
  that points at one of these.
- Study rooms: each library's /spaces page lists its bookable rooms with
  capacities, and /spaces/availability/grid returns every 30 minute slot for
  a date range, flagged when booked. Slots exist only inside booking hours, so
  the union of a day's slots is that day's open window and a missing slot
  inside it is reserved. Anyone with a UCLA Logon can book.

Like hill.json, this changes by the minute: hill_api.py serves collect() live
and the Pages workflow refreshes the bundled snapshot before every deploy.

Usage:
    python scrape_library.py [--days 4] [--file library.json]
"""

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrape import HEADERS

BASE = 'https://calendar.library.ucla.edu'
IID = 3244  # UCLA Library's LibCal institution id (from the /spaces page)
LA = ZoneInfo('America/Los_Angeles')

# Building code (matches classrooms.json / buildings.json) for each LibCal
# spaces location. Room names carry the library name already.
LOCATIONS = {
    4361: {'library': 'Powell Library', 'building': 'POWELL'},
    5567: {'library': 'Young Research Library', 'building': 'YRL'},
    4752: {'library': 'Music Library', 'building': 'SCHOENBERG'},
    6578: {'library': 'Biomedical Library', 'building': 'CHS'},
    8312: {'library': 'Science and Engineering Library', 'building': 'BOELTER'},
    19391: {'library': 'Powell Library Media Lab', 'building': 'POWELL'},
}
# Rooms whose name says which building they are in (SEL has rooms in two).
BUILDING_IN_NAME = [(re.compile(r'^Geology\b', re.I), 'GEOLOGY'), (re.compile(r'^Boelter\b', re.I), 'BOELTER')]

SLOT = 30  # minutes


class ScrapeError(Exception):
    pass


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.headers['Referer'] = BASE + '/spaces'
    return s


def to_min(hhmm):
    """'2026-09-25 16:30:00' -> 990; a midnight end means end of day."""
    h, m = int(hhmm[11:13]), int(hhmm[14:16])
    return h * 60 + m


def parse_hour(text):
    """'8am' / '9:30pm' / '12am' -> minutes. Returns None for junk."""
    m = re.match(r'^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*$', text, re.I)
    if not m:
        return None
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == 'pm' else 0)
    return h * 60 + int(m.group(2) or 0)


# ---------- hours ----------

def fetch_hours(session, weeks=2):
    """Return {lid: {'name', 'parent', 'days': {date: [[open, close], ...] | [] | None}}}.

    [] is closed all day; None means no hours were posted. A range that ends at or before it starts (Night Powell, "10pm - 8am") runs
    past midnight: the part after midnight is added to the next date.
    """
    r = session.get(f"{BASE}/api_hours_grid.php", params={'iid': IID, 'lid': 0, 'format': 'json', 'weeks': weeks}, timeout=60)
    r.raise_for_status()
    out = {}
    for loc in r.json().get('locations', []):
        days = {}
        overflow = {}
        for week in loc.get('weeks', []):
            for day in week.values():
                date = day.get('date')
                t = day.get('times') or {}
                ranges = []
                if t.get('status') not in ('24hours', 'open', 'closed'):
                    days[date] = None  # hours not posted ("not-set", free text)
                    continue
                if t.get('status') == '24hours':
                    ranges = [[0, 24 * 60]]
                elif t.get('status') == 'open':
                    for h in t.get('hours', []):
                        a, b = parse_hour(h.get('from', '')), parse_hour(h.get('to', ''))
                        if a is None or b is None:
                            continue
                        if b <= a:  # crosses midnight
                            if b > 0:
                                nxt = (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat()
                                overflow.setdefault(nxt, []).append([0, b])
                            b = 24 * 60
                        ranges.append([a, b])
                days[date] = sorted(ranges)
        for date, extra in overflow.items():
            if days.get(date) is not None:
                days[date] = sorted(days[date] + extra)
        out[loc['lid']] = {'name': loc.get('name', '').strip(), 'category': loc.get('category'),
                           'parent': loc.get('parent_lid'), 'days': days}
    if not out:
        raise ScrapeError('hours API returned no locations')
    return out


# ---------- study rooms ----------

RES_RE = re.compile(r'resources\.push\(\{(.*?)\}\);', re.S)
KV_RE = re.compile(r'(\w+):\s*("(?:[^"\\]|\\.)*"|[\w.-]+)')
CAP_RE = re.compile(r'\s*\(Capacity\s+(\d+)\)\s*$', re.I)


def list_rooms(session, lid):
    """Rooms shown on a location's public /spaces page: [{eid, gid, title, capacity}]."""
    r = session.get(f"{BASE}/spaces", params={'lid': lid, 'gid': 0}, timeout=60)
    r.raise_for_status()
    rooms = []
    for block in RES_RE.findall(r.text):
        d = {}
        for k, v in KV_RE.findall(block):
            d[k] = json.loads(v) if v.startswith('"') else v
        if not d.get('eid'):
            continue
        title = d.get('title', '')
        cap = CAP_RE.search(title)
        rooms.append({
            'eid': int(d['eid']), 'gid': int(d.get('gid') or 0),
            'title': CAP_RE.sub('', title).strip(),
            'capacity': int(cap.group(1)) if cap else (int(d['capacity']) if str(d.get('capacity', '')).isdigit() else None),
        })
    return rooms


def fetch_grid(session, lid, gid, start, end):
    """All slots for one location/group over [start, end). Retries on a flaky answer."""
    data = {'lid': lid, 'gid': gid, 'eid': -1, 'seat': 0, 'seatId': 0, 'zone': 0,
            'start': start, 'end': end, 'pageIndex': 0, 'pageSize': 200}
    for attempt in range(4):
        try:
            r = session.post(f"{BASE}/spaces/availability/grid", data=data, timeout=60)
            r.raise_for_status()
            return r.json().get('slots', [])
        except (requests.RequestException, ValueError):
            time.sleep(0.5 * 2 ** attempt)
    return None


def merge_free(slots):
    """Sorted [s, e] 30 min slots -> merged free ranges."""
    out = []
    for s, e in slots:
        if out and out[-1][1] >= s:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def collect(days=4, workers=6):
    """Scrape hours and room availability; returns the library.json structure."""
    session = make_session()
    hours = fetch_hours(session)
    today = datetime.now(LA).date()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(days)]
    end = (today + timedelta(days=days)).isoformat()

    catalog = {}  # eid -> room record
    groups = set()
    for lid, info in LOCATIONS.items():
        for room in list_rooms(session, lid):
            building = info['building']
            for rx, code in BUILDING_IN_NAME:
                if rx.search(room['title']):
                    building = code
            catalog[room['eid']] = {
                'text': room['title'],
                'building': building,
                'room': room['title'],
                'library': info['library'],
                'capacity': room['capacity'],
                'type': 'Library study room',
                'characteristics': [],
                'url': f"{BASE}/space/{room['eid']}",
                'lid': lid, 'gid': room['gid'],
                'days': {},
            }
            groups.add((lid, room['gid']))
    if not catalog:
        raise ScrapeError('no rooms found on the LibCal spaces pages')

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda g: (g, fetch_grid(session, g[0], g[1], dates[0], end)), sorted(groups)))
    failed = [g for g, slots in results if slots is None]
    if len(failed) > len(results) // 2:
        raise ScrapeError(f"{len(failed)}/{len(results)} availability requests failed")
    failed = set(failed)

    # (eid, date) -> {'free': [...], 'hours': [open, close]}
    for (lid, gid), slots in results:
        if slots is None:
            continue
        for s in slots:
            room = catalog.get(s.get('itemId'))
            if not room:
                continue  # rooms not on the public list (staff, hidden)
            date = s['start'][:10]
            if date not in dates:
                continue
            a = to_min(s['start'])
            b = to_min(s['end'])
            if b <= a or s['end'][:10] != date or b >= 24 * 60 - 1:  # "23:59" = end of day
                b = 24 * 60
            day = room['days'].setdefault(date, {'free': [], 'hours': [a, b]})
            day['hours'][0], day['hours'][1] = min(day['hours'][0], a), max(day['hours'][1], b)
            if not s.get('className'):  # no class = available
                day['free'].append([a, b])

    rooms = []
    for eid, room in catalog.items():
        if (room['lid'], room['gid']) in failed:
            room['days'] = {}  # unknown: the page falls back to its snapshot
        elif not any(d.get('hours') for d in room['days'].values()):
            continue  # nothing bookable in the window (e.g. YRL pods): not a study room today
        for date in dates:
            if (room['lid'], room['gid']) in failed:
                break
            day = room['days'].setdefault(date, {'free': [], 'hours': None})  # no slots = closed
            day['free'] = merge_free(sorted(day['free']))
        room.pop('lid'), room.pop('gid')
        rooms.append(room)

    return {
        'updated': datetime.now(LA).isoformat(timespec='seconds'),
        'source': BASE + '/spaces',
        'requests_failed': len(failed),
        'dates': dates,
        'hours': hours,
        'rooms': sorted(rooms, key=lambda r: (r['library'], r['text'])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=4, help='days of availability to pull (the site books ~3 days ahead)')
    ap.add_argument('--file', default='library.json')
    args = ap.parse_args()
    try:
        out = collect(args.days)
    except (ScrapeError, requests.RequestException) as e:
        sys.exit(f"ERROR: {e}, not saving")
    with open(args.file, 'w') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    print(f"{len(out['rooms'])} rooms, {len(out['hours'])} hours locations, {args.days} days, {out['requests_failed']} requests failed")


if __name__ == '__main__':
    main()
