"""
Scrape Hill study room availability from Residential Life's reservation site
(reserve.reslife.ucla.edu) into hill.json.

The site lists every open one-hour slot per room for the next two weeks,
publicly. A slot missing inside a building's open hours is reserved. These rooms
can only be booked by on-campus residents.

The data changes by the minute. hill_api.py serves collect() live so the page
can refresh on every open; the Pages workflow also runs this before each deploy
so the bundled hill.json snapshot stays reasonably fresh as a fallback.

It also adds the open (no reservation) study spaces that Residential Life
lists with hours, like Covel 227 (see HILL_SPACES).

Usage:
    python scrape_hill.py [--days 8] [--file hill.json]
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrape import HEADERS

try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'buildings.json')) as _f:
        KNOWN_BUILDINGS = set(json.load(_f)['buildings'])
except (OSError, ValueError, KeyError):
    KNOWN_BUILDINGS = set()

BASE = 'https://reserve.reslife.ucla.edu'
LA = ZoneInfo('America/Los_Angeles')
# Only study spaces, not music rooms, studios or kitchens.
SKIP_RE = re.compile(r'music|movement|kitchen|studio', re.I)
CAP_RE = re.compile(r'max\s+(\d+)\s+people', re.I)
ROOM_RE = re.compile(r'^(?P<building>.*?)\s*(?:study|meeting)?\s*room\s+#?(?P<room>\w+)$', re.I)


def space_cap(description):
    """'rooms for 2-5 people' -> 5"""
    m = re.search(r'(\d+)\s*-\s*(\d+)\s+people', description)
    return int(m.group(2)) if m else None


def clean_title(title):
    """'Olympic Study Room #375B (3824)' -> ('Olympic Study Room 375B', None, [])"""
    cap = CAP_RE.search(title)
    notes = [n.strip() for p in re.findall(r'\(([^)]*)\)', title) for n in p.split(',')
             if n.strip() and not CAP_RE.search(n) and not n.strip().isdigit()]
    name = re.sub(r'\s*\([^)]*\)', '', title).replace('#', '').strip()
    return ' '.join(name.split()), int(cap.group(1)) if cap else None, notes


def building_code(name):
    """'De Neve Meeting Rooms' -> 'DE NEVE': match the codes in buildings.json
    so the page can look up the building's area and location."""
    name = ' '.join(name.upper().split())
    if name in KNOWN_BUILDINGS:
        return name
    for code in sorted(KNOWN_BUILDINGS, key=len, reverse=True):
        if name.startswith(code + ' '):
            return code
    return name


def to_min(t):
    dt = datetime.strptime(t.strip(), '%I:%M %p')
    return dt.hour * 60 + dt.minute


def list_spaces(session):
    soup = BeautifulSoup(session.get(BASE + '/reserve', timeout=30).text, 'html.parser')
    spaces = {}
    for a in soup.select('a[href^="/reserve/"]'):
        sid = a['href'].split('/')[2].split('?')[0]
        title = a.select_one('.reserve-sets--title')
        desc = a.select_one('.reserve-sets--description')
        name = title.get_text(' ', strip=True) if title else a.get_text(' ', strip=True)[:60]
        if SKIP_RE.search(name):
            continue
        spaces[sid] = {'name': name, 'description': desc.get_text(' ', strip=True) if desc else ''}
    return spaces


def parse_slots(html):
    soup = BeautifulSoup(html, 'html.parser')
    for label in soup.select('.reserve-option--label'):
        t = label.select_one('.reserve-option--time')
        title = label.select_one('.reserve-option--title')
        if not t or not title or ' - ' not in t.get_text():
            continue
        start, end = t.get_text(strip=True).split(' - ')
        s, e = to_min(start), to_min(end)
        yield s, e if e > s else 24 * 60, title.get_text(' ', strip=True)


class ScrapeError(Exception):
    pass


# Open study spaces on the Hill, from "Study Spaces on the Hill" on
# Residential Life's academics page. Their hours are only posted as text on
# that page, so they're kept here and checked against the page on every run:
# an entry whose posted text no longer appears is left out rather than shown
# with hours that may be stale. Minutes after midnight; 12 AM close = 1440.
SPACES_URL = 'https://reslife.ucla.edu/academics'
ALL_DAY = [[0, 24 * 60]]
HILL_SPACES = [
    # (text, building, room, weekly hours by weekday 0=Mon, posted text on the page)
    ('Covel 227', 'COVEL', '227', {d: [[6 * 60, 24 * 60]] for d in range(7)},
     'Covel 227: Everyday 6 AM to 12 AM'),
    ('Sproul Landing Living Room', 'SPROUL LANDING', 'Living Room', {d: ALL_DAY for d in range(7)},
     'Sproul Landing Living Room, Hedrick Study, & Olympic Hall Understory & Canopy: 24 Hour Study Space'),
    ('Hedrick Study', 'HEDRICK', 'Hedrick Study', {d: ALL_DAY for d in range(7)},
     'Sproul Landing Living Room, Hedrick Study, & Olympic Hall Understory & Canopy: 24 Hour Study Space'),
    ('Olympic Hall Understory & Canopy', 'OLYMPIC', 'Understory & Canopy', {d: ALL_DAY for d in range(7)},
     'Sproul Landing Living Room, Hedrick Study, & Olympic Hall Understory & Canopy: 24 Hour Study Space'),
    ('Sunset Village Learning Center (150)', 'SUNSET VILLAGE', '150',
     {0: [[720, 1440]], 1: [[720, 1440]], 2: [[720, 1440]], 3: [[720, 1440]],
      4: [[720, 1020]], 5: [[720, 1200]], 6: [[1080, 1440]]},
     'Sunset Village Learning Center (150): Monday - Thursday: 12 PM - 12 AM, Friday: 12 PM - 5 PM, '
     'Saturday: 12 PM - 8 PM, Sunday: 6 PM - 12 AM'),
    ('De Neve Learning Center (P333)', 'DE NEVE', 'P333',
     {0: [[720, 1440]], 1: [[720, 1440]], 2: [[720, 1440]], 3: [[720, 1440]],
      4: [[720, 1020]], 5: [], 6: [[1080, 1440]]},
     'De Neve Learning Center (P333): Monday - Thursday: 12 PM - 12 AM, Friday: 12 PM - 5 PM, '
     'Sunday: 6 PM - 12 AM'),
]
# Not included: "Epicuria at Covel: Open Week 4, Sunday through Thursday 10 PM
# to 2 AM". Which dates "Week 4" covers isn't on the page.


def hill_spaces(session, dates):
    """Hours-based entries for HILL_SPACES, in the same shape as the study rooms."""
    try:
        page = BeautifulSoup(session.get(SPACES_URL, timeout=30).text, 'html.parser').get_text(' ')
        page = ' '.join(page.split())
    except requests.RequestException as e:
        print(f"could not check {SPACES_URL} ({e}); using the hours on file", file=sys.stderr)
        page = None
    out = []
    for text, building, room, weekly, posted in HILL_SPACES:
        if page is not None and posted not in page:
            print(f"{text}: posted hours changed on {SPACES_URL}, leaving it out", file=sys.stderr)
            continue
        days = {}
        for d in dates:
            ranges = weekly.get(datetime.strptime(d, '%Y-%m-%d').weekday(), [])
            days[d] = ({'free': [list(r) for r in ranges], 'hours': [ranges[0][0], ranges[-1][1]]}
                       if ranges else {'free': [], 'hours': None, 'closed': [[0, 24 * 60]]})
        out.append({
            'text': text, 'building': building, 'room': room, 'space': 'Study Spaces on the Hill',
            'description': f'Open study space listed by Residential Life: "{posted}". '
                           'No reservation. Hours may differ during breaks.',
            'capacity': None, 'type': 'Hill study space', 'characteristics': [],
            'url': SPACES_URL, 'area': 'hill', 'days': days,
        })
    return out


def collect(days=8, workers=12):
    """Scrape the reservation site and return the hill.json structure."""
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(pool_connections=workers, pool_maxsize=workers)
    session.mount('https://', adapter)
    spaces = list_spaces(session)
    if not spaces:
        raise ScrapeError('no spaces found on the reservation site')
    today = datetime.now(LA).date()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(days)]
    jobs = [(sid, d) for sid in spaces for d in dates]

    def get(job):
        sid, d = job
        url = f"{BASE}/reserve/{sid}?date={d}&duration=PT1H"
        for attempt in range(4):
            try:
                r = session.get(url, timeout=20)
                r.raise_for_status()
                return job, list(parse_slots(r.text))
            except requests.RequestException:
                time.sleep(0.5 * 2 ** attempt)  # the site drops bursts; back off
        return job, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(get, jobs))

    failed_pages = {job for job, slots in results if slots is None}
    failed = len(failed_pages)
    if failed > len(jobs) // 2:
        raise ScrapeError(f"{failed}/{len(jobs)} pages failed")

    rooms = {}
    hours = {}  # (space, date) -> [open, close] from the union of all slots
    for (sid, d), slots in results:
        if slots is None:
            continue
        space = spaces[sid]['name']
        for s, e, title in slots:
            name, cap, notes = clean_title(title)
            rm = ROOM_RE.match(name)
            building = rm.group('building').strip() if rm and rm.group('building').strip() else re.sub(r'\s*((study|meeting)\s+rooms?|the study at)\s*', ' ', space, flags=re.I).strip()
            if not name.lower().startswith(building.lower()):
                name = f"{building} {name}"
            room = rooms.setdefault(name, {
                'text': name,
                'building': building_code(building),
                'room': rm.group('room') if rm else name,
                'space': space,
                'description': spaces[sid]['description'],
                'capacity': cap or space_cap(spaces[sid]['description']),
                'type': 'Hill study room',
                'characteristics': notes,
                'url': f"{BASE}/reserve/{sid}",
                'sid': sid,
                'days': {},
            })
            day = room['days'].setdefault(d, {'free': []})
            day['free'].append([s, e])
            h = hours.setdefault((sid, d), [s, e])
            h[0], h[1] = min(h[0], s), max(h[1], e)

    # Record open hours on every room for every date we fetched, so a room with
    # no free slot that day shows as fully reserved rather than missing.
    for room in rooms.values():
        sid = room.pop('sid')
        for d in dates:
            if (sid, d) in failed_pages:
                # Unknown, not "fully reserved": leave the day out so the page
                # can fall back to its snapshot for it.
                room['days'].pop(d, None)
                continue
            day = room['days'].setdefault(d, {'free': []})
            day['hours'] = hours.get((sid, d))
            day['free'].sort()

    return {
        'updated': datetime.now(LA).isoformat(timespec='seconds'),
        'source': BASE + '/reserve',
        'pages_failed': failed,
        'dates': dates,
        'rooms': sorted(rooms.values(), key=lambda r: r['text']) + hill_spaces(session, dates),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=8)
    ap.add_argument('--file', default='hill.json')
    args = ap.parse_args()
    try:
        out = collect(args.days)
    except ScrapeError as e:
        sys.exit(f"ERROR: {e}, not saving")
    with open(args.file, 'w') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    print(f"{len(out['rooms'])} rooms, {args.days} days, {out['pages_failed']} pages failed")


if __name__ == '__main__':
    main()
