"""
Scrape Hill study room availability from Residential Life's reservation site
(reserve.reslife.ucla.edu) into hill.json.

The site lists every open one-hour slot per room for the next two weeks,
publicly. A slot missing inside a building's open hours is reserved. These rooms
can only be booked by on-campus residents.

The data changes by the minute, so the Pages workflow runs this right before
each deploy (every 30 minutes) instead of committing it.

Usage:
    python scrape_hill.py [--days 8] [--file hill.json]
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrape import HEADERS

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=8)
    ap.add_argument('--file', default='hill.json')
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)
    spaces = list_spaces(session)
    if not spaces:
        sys.exit('ERROR: no spaces found on the reservation site')
    today = datetime.now(LA).date()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(args.days)]
    jobs = [(sid, d) for sid in spaces for d in dates]

    def get(job):
        sid, d = job
        url = f"{BASE}/reserve/{sid}?date={d}&duration=PT1H"
        for attempt in range(3):
            try:
                r = session.get(url, timeout=30)
                r.raise_for_status()
                return job, list(parse_slots(r.text))
            except requests.RequestException:
                pass
        return job, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(get, jobs))

    failed = sum(1 for _, slots in results if slots is None)
    if failed > len(jobs) // 2:
        sys.exit(f"ERROR: {failed}/{len(jobs)} pages failed, not saving")

    rooms = {}
    hours = {}  # (space, date) -> [open, close] from the union of all slots
    for (sid, d), slots in results:
        if slots is None:
            continue
        space = spaces[sid]['name']
        for s, e, title in slots:
            name, cap, notes = clean_title(title)
            rm = ROOM_RE.match(name)
            building = rm.group('building').strip() if rm and rm.group('building').strip() else re.sub(r'\s*(study\s+rooms?|the study at)\s*', ' ', space, flags=re.I).strip()
            if not name.lower().startswith(building.lower()):
                name = f"{building} {name}"
            room = rooms.setdefault(name, {
                'text': name,
                'building': building.upper(),
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
            day = room['days'].setdefault(d, {'free': []})
            day['hours'] = hours.get((sid, d))
            day['free'].sort()

    out = {
        'updated': datetime.now(LA).isoformat(timespec='minutes'),
        'source': BASE + '/reserve',
        'rooms': sorted(rooms.values(), key=lambda r: r['text']),
    }
    with open(args.file, 'w') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    print(f"{len(spaces)} spaces, {len(rooms)} rooms, {len(dates)} days, {failed} pages failed")


if __name__ == '__main__':
    main()
