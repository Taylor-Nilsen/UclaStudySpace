"""
Scrape UCLA classroom schedules into classrooms.json.

The registrar's ClassroomDetail page embeds the whole term calendar as JSON in
a <script> tag, so plain HTTP requests are enough. No browser needed.

Usage:
    python scrape.py [--term 26F] [--limit N] [--workers 8]

If --term is omitted, the current/upcoming UCLA term is picked from today's date.
"""

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
DAY_CODES = {'M': 'Monday', 'T': 'Tuesday', 'W': 'Wednesday', 'R': 'Thursday',
             'F': 'Friday', 'S': 'Saturday', 'U': 'Sunday'}
CALENDAR_RE = re.compile(r"createFullCalendar\(\$\.parseJSON\('(.*?)'\)\)", re.S)
ENR_RE = re.compile(r'Enr:\s*(\d+)\s*of\s*(\d+)')
TERM_RE = re.compile(r'term=\d{2}[WSUF]')
HEADERS = {'User-Agent': 'UclaStudySpace/2.0 (+https://github.com/Taylor-Nilsen/UclaStudySpace)'}


def current_term(today=None):
    """Return the term code (e.g. '26F') for the current or about-to-start quarter.

    Cutoffs sit ~2 weeks before each quarter starts so a run right before a new
    quarter already targets it.
    """
    today = today or date.today()
    md = (today.month, today.day)
    yy = today.year % 100
    if md >= (12, 15):
        return f"{(yy + 1) % 100:02d}W"
    if md < (3, 10):
        return f"{yy:02d}W"
    if md < (6, 10):
        return f"{yy:02d}S"
    if md < (9, 10):
        return f"{yy:02d}U"
    return f"{yy:02d}F"


def fmt_time(t):
    return t.strftime('%I:%M %p')


def parse_page(html):
    """Parse a ClassroomDetail page into (schedule or None, characteristics)."""
    soup = BeautifulSoup(html, 'html.parser')

    characteristics = []
    ul = soup.find('ul', id='characteristics-list')
    if ul:
        for li in ul.find_all('li'):
            # The page sits inside a <template>, and get_text() skips
            # TemplateString nodes, so read .string directly.
            text = (li.string or '').strip()
            if text:
                characteristics.append(text)

    match = CALENDAR_RE.search(html)
    if not match:
        return None, characteristics
    try:
        events = json.loads(match.group(1).replace("\\'", "'").replace('\\"', '"'))
    except json.JSONDecodeError:
        return None, characteristics
    if not events:
        return None, characteristics

    schedule = {day: [] for day in DAYS}
    seen = set()
    for event in events:
        start_str, end_str = event.get('start') or '', event.get('end') or ''
        try:
            if 'T' in start_str:
                start = datetime.fromisoformat(start_str)
                end = datetime.fromisoformat(end_str) if 'T' in end_str else None
                days = [start.strftime('%A')]
            else:
                start = datetime.strptime(event['strt_time'], '%H:%M:%S')
                end = datetime.strptime(event['stop_time'], '%H:%M:%S') if event.get('stop_time') else None
                days = [DAY_CODES[c] for c in (event.get('Days_in_week') or '') if c in DAY_CODES]
        except (KeyError, ValueError):
            continue

        enr = ENR_RE.search(event.get('enrollment') or '')
        enrolled = int(enr.group(1)) if enr else event.get('enroll_total')
        capacity = int(enr.group(2)) if enr else event.get('enroll_capacity')
        course = ' '.join((event.get('title') or '').split())
        kind = (event.get('lecture') or '').strip()

        for day in days:
            key = (day, course, kind, start.time(), end.time() if end else None)
            if key in seen:
                continue
            seen.add(key)
            schedule[day].append({
                'course': course,
                'type': kind,
                'start_time': fmt_time(start),
                'end_time': fmt_time(end) if end else '',
                'enrolled': enrolled,
                'capacity': capacity,
                '_sort': start.hour * 60 + start.minute,
            })

    for day in schedule:
        schedule[day].sort(key=lambda e: e['_sort'])
        for e in schedule[day]:
            del e['_sort']
    return schedule, characteristics


def fetch(session, url, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def scrape_room(session, room):
    html = fetch(session, room['url'])
    schedule, characteristics = parse_page(html)
    room['characteristics'] = characteristics
    room['schedule'] = schedule
    room['no_calendar'] = schedule is None
    return room


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--term', default=None, help='Term code like 26F (default: auto)')
    ap.add_argument('--limit', type=int, default=0, help='Only scrape the first N offered rooms')
    ap.add_argument('--workers', type=int, default=8, help='Concurrent requests (default 8)')
    ap.add_argument('--file', default='classrooms.json')
    args = ap.parse_args()

    term = args.term or current_term()
    if not re.fullmatch(r'\d{2}[WSUF]', term):
        sys.exit(f"ERROR: bad term code {term!r}")

    with open(args.file) as f:
        rooms = json.load(f)

    for room in rooms:
        if room.get('url'):
            room['url'] = TERM_RE.sub(f'term={term}', room['url'])

    todo = [r for r in rooms if r.get('offered')]
    if args.limit > 0:
        todo = todo[:args.limit]
    print(f"Term {term} | {len(todo)} rooms | {args.workers} workers")

    stats = {'ok': 0, 'empty': 0, 'failed': 0}
    started = time.time()
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(pool_connections=args.workers, pool_maxsize=args.workers)
    session.mount('https://', adapter)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scrape_room, session, room): room for room in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            room = futures[fut]
            try:
                fut.result()
                if room['no_calendar']:
                    stats['empty'] += 1
                    status = 'no classes'
                else:
                    stats['ok'] += 1
                    status = f"{sum(len(v) for v in room['schedule'].values())} events"
            except Exception as e:
                room['schedule'] = None
                room['no_calendar'] = None
                stats['failed'] += 1
                status = f"FAILED ({e})"
            print(f"[{i}/{len(todo)}] {room.get('text')}: {status}")

    # Refuse to overwrite good data with a mostly failed run.
    if todo and stats['failed'] > len(todo) // 2:
        sys.exit(f"ERROR: {stats['failed']}/{len(todo)} rooms failed, not saving")

    with open(args.file, 'w') as f:
        json.dump(rooms, f, indent=1)
        f.write('\n')

    print(f"Done in {time.time() - started:.0f}s | ok {stats['ok']} | no classes {stats['empty']} | failed {stats['failed']}")


if __name__ == '__main__':
    main()
