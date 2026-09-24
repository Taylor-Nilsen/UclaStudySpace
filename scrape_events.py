"""
Pull dated non-class events (clubs, seminars, talks, workshops) held in our
classrooms and attach them to classrooms.json as room['events'].

The registrar grids only show academic classes, and room reservations made
through the UCLA Events Office are not published anywhere. So this reads the
public calendars that do list rooms:
  - UCLA Community (community.ucla.edu): term pages, every program, club sports
  - Department iCal feeds (WordPress "The Events Calendar", /events/?ical=1)
and keeps the events whose location names one of our classrooms.

Usage:
    python scrape_events.py [--term 26F]
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

from scrape import HEADERS, current_term

# Department calendars with public iCal feeds (checked Sept 2026).
ICAL_FEEDS = {
    'Computer Science': 'https://www.cs.ucla.edu/events/?ical=1',
    'Civil & Environmental Engineering': 'https://www.cee.ucla.edu/events/?ical=1',
    'Mechanical & Aerospace Engineering': 'https://www.mae.ucla.edu/events/?ical=1',
    'Bioengineering': 'https://www.bioeng.ucla.edu/events/?ical=1',
    'Chemical & Biomolecular Engineering': 'https://www.chemeng.ucla.edu/events/?ical=1',
    'Materials Science & Engineering': 'https://www.mse.ucla.edu/events/?ical=1',
    'UCLA Samueli': 'https://samueli.ucla.edu/events/?ical=1',
    'Chemistry & Biochemistry': 'https://www.chemistry.ucla.edu/events/?ical=1',
    'Economics': 'https://econ.ucla.edu/events/?ical=1',
    'History': 'https://history.ucla.edu/events/?ical=1',
    'Communication': 'https://comm.ucla.edu/events/?ical=1',
    'Luskin School of Public Affairs': 'https://luskin.ucla.edu/events/?ical=1',
    'Philosophy': 'https://philosophy.ucla.edu/events/?ical=1',
    'Linguistics': 'https://www.linguistics.ucla.edu/events/?ical=1',
    'MCDB': 'https://www.mcdb.ucla.edu/events/?ical=1',
    'Humanities': 'https://www.humanities.ucla.edu/events/?ical=1',
    'Classics': 'https://classics.ucla.edu/events/?ical=1',
    'Center for Medieval & Renaissance Studies': 'https://cmrs.ucla.edu/events/?ical=1',
    'Center for the Study of Women': 'https://csw.ucla.edu/events/?ical=1',
    'Near Eastern Languages & Cultures': 'https://nelc.ucla.edu/events/?ical=1',
    'Art History': 'https://arthistory.ucla.edu/events/?ical=1',
    'Musicology': 'https://musicology.ucla.edu/events/?ical=1',
    'Ethnomusicology': 'https://ethnomusic.ucla.edu/events/?ical=1',
    'African American Studies': 'https://afam.ucla.edu/events/?ical=1',
    'Chicana/o & Central American Studies': 'https://www.chicano.ucla.edu/events/?ical=1',
    'Architecture & Urban Design': 'https://www.arch.ucla.edu/events/?ical=1',
}
# Feeds carry years of history; keep a window around today.
KEEP_PAST_DAYS, KEEP_FUTURE_DAYS = 7, 150

BASE = 'https://community.ucla.edu'
LA = ZoneInfo('America/Los_Angeles')
SEASONS = {'W': 'winter', 'S': 'spring', 'U': 'summer', 'F': 'fall'}

# Building code in classrooms.json -> regex for how people write it.
BUILDING_ALIASES = {
    'BOELTER': r'boelter|\bBH\b',
    'BUNCHE': r'bunche',
    'PUB AFF': r'public\s+affairs|pub\.?\s*aff',
    'MS': r'math(?:ematical|\.)?\s*sci(?:ences?|\.)?(?:\s+building)?|\bMSB?\b',
    'HAINES': r'haines',
    'KAPLAN': r'kaplan',
    'DODD': r'dodd',
    'ROLFE': r'rolfe',
    'ROYCE': r'royce',
    'WGYOUNG': r'(?:w\.?\s*g\.?|william\s+g\.?)\s*young|young\s+hall',
    'PAB': r'\bPAB\b|physics\s*(?:and|&)\s*astronomy',
    'FRANZ': r'franz',
    'GEOLOGY': r'geology',
    'LAKRETZ': r'la\s*kretz\s+hall',
    'KAUFMAN': r'kaufman',
    'KNSY PV': r'kinsey',
    'BROAD': r'broad\s+art',
    'FOWLER': r'fowler',
    'MOORE': r'moore\s+hall|\bmoore\b',
    'PERLOFF': r'perloff',
    'SLICHTR': r'slichter',
}
BUILDING_RES = {code: re.compile(p, re.I) for code, p in BUILDING_ALIASES.items()}
ROOM_TOKEN_RE = re.compile(r'\b([A-Z]{0,2})\s?0*(\d{1,5}[A-Z]?)\b', re.I)
# Words allowed between the building name and the room number ("Hall, Room 5200").
FILLER_RE = re.compile(r'^(?:[\s,.:#-]|hall\b|pavilion\b|building\b|bldg\b|rooms?\b|rm\b)*', re.I)
# One or more room numbers right there: "200", "200 & 208", "CS 50".
ROOM_LIST_RE = re.compile(r'[A-Z]{0,2}\s?\d{1,5}[A-Z]?\b(?:\s*(?:&|and|,|/)\s*[A-Z]{0,2}\s?\d{1,5}[A-Z]?\b)*', re.I)


def norm_room(room):
    """'02444' -> '2444', 'A00214' -> 'A214', 'CS 24' -> 'CS24'."""
    m = re.match(r'^\s*([A-Z]*)\s*0*(\d+\w*)\s*$', room, re.I)
    return (m.group(1) + m.group(2)).upper() if m else room.strip().upper()


ROOM_BEFORE_RE = re.compile(r'(?:^|[\s,(])((?:[A-Z]{1,2}\s?)?\d{1,5}[A-Z]?)(?:\s*,)?\s*(?:UCLA\s+)?$', re.I)


def match_rooms(location, room_index):
    """Return classroom indexes the free-text location points at."""
    hits = []
    for code, rx in BUILDING_RES.items():
        m = rx.search(location)
        if not m:
            continue
        rooms = room_index.get(code, {})
        # Only look at the room number(s) directly after the building name, so
        # "Dodd family ... Parking Structure 121" does not become DODD 121.
        rest = location[m.end():]
        rest = rest[FILLER_RE.match(rest).end():]
        near = ROOM_LIST_RE.match(rest)
        # Or directly before it: "3400 Boelter Hall", "Room 3400, Boelter".
        before = ROOM_BEFORE_RE.search(location[:m.start()])
        tokens = (near.group(0) if near else '') + ' ' + (before.group(1) if before else '')
        for prefix, num in ROOM_TOKEN_RE.findall(tokens):
            key = (prefix + num).upper()
            if key in rooms:
                hits.append(rooms[key])
            elif num.upper() in rooms:
                hits.append(rooms[num.upper()])
    return list(dict.fromkeys(hits))


def term_pages(term):
    slug = f"/term/{SEASONS[term[2]]}-20{term[:2]}"
    html = requests.get(BASE + '/calendars', headers=HEADERS, timeout=30).text
    links = set(re.findall(r'href="(%s/[^"]+)"' % re.escape(slug), html))
    if not links:  # past terms drop off the index page; guess the week URLs
        links = {f"{slug}/week-{i}" for i in range(11)} | {f"{slug}/finals-week"}
    # Each program / club sport page lists its upcoming events, including some
    # that never make the term pages.
    for index, kind in (('/programs', 'program'), ('/clubsports', 'clubsport')):
        try:
            html = requests.get(BASE + index, headers=HEADERS, timeout=30).text
            links |= set(re.findall(r'href="(/%s/[^"]+)"' % kind, html))
        except requests.RequestException:
            pass
    return sorted(links)


def parse_events(html):
    soup = BeautifulSoup(html, 'html.parser')
    for card in soup.select('.event-card'):
        loc = card.select_one('.event-card-location:not(.event-card-virtuallocation)')
        time_el = card.select_one('.event-card-time')
        if not loc or (time_el and time_el.get('data-event-allday') == 'true'):
            continue
        start, stop = card.get('data-event-start'), card.get('data-event-stop')
        if not start or not stop:
            continue
        link = card.select_one('.event-card-summary a')
        org = card.select_one('.event-card-category a')
        yield {
            'id': card.get('id', ''),
            'title': link.get_text(strip=True) if link else 'Event',
            'url': BASE + link['href'] if link and link.get('href', '').startswith('/') else '',
            'org': org.get_text(strip=True) if org else '',
            'location': loc.get_text(' ', strip=True),
            'start': datetime.fromisoformat(start.replace('Z', '+00:00')).astimezone(LA),
            'stop': datetime.fromisoformat(stop.replace('Z', '+00:00')).astimezone(LA),
        }


def unfold_ics(text):
    return re.sub(r'\r?\n[ \t]', '', text).replace('\r', '')


def ics_value(v):
    return v.replace('\\n', ' ').replace('\\,', ',').replace('\\;', ';').replace('\\\\', '\\').strip()


def ics_time(key, value):
    """Parse DTSTART/DTEND into an aware LA datetime; None for all-day."""
    if 'VALUE=DATE' in key and 'T' not in value:
        return None
    if value.endswith('Z'):
        return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=ZoneInfo('UTC')).astimezone(LA)
    return datetime.strptime(value[:15], '%Y%m%dT%H%M%S').replace(tzinfo=LA)


def parse_ics(text, org):
    for block in unfold_ics(text).split('BEGIN:VEVENT')[1:]:
        props = {}
        for line in block.split('\n'):
            if ':' in line and not line.startswith(' '):
                key, value = line.split(':', 1)
                props.setdefault(key.split(';')[0], (key, value))
        if 'DTSTART' not in props or 'LOCATION' not in props or 'RRULE' in props:
            continue
        try:
            start = ics_time(*props['DTSTART'])
            stop = ics_time(*props['DTEND']) if 'DTEND' in props else None
        except ValueError:
            continue
        if not start:
            continue
        yield {
            'id': props.get('UID', ('', ''))[1] or f"{org}{props['DTSTART'][1]}",
            'title': ics_value(props.get('SUMMARY', ('', 'Event'))[1]),
            'url': ics_value(props.get('URL', ('', ''))[1]),
            'org': org,
            'location': ics_value(props['LOCATION'][1]),
            'start': start,
            'stop': stop or start + timedelta(hours=1),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--term', default=None)
    ap.add_argument('--file', default='classrooms.json')
    args = ap.parse_args()
    term = args.term or current_term()

    with open(args.file) as f:
        rooms = json.load(f)

    room_index = {}
    for i, r in enumerate(rooms):
        if r.get('offered') and r.get('building') and r.get('room'):
            room_index.setdefault(r['building'], {})[norm_room(r['room'])] = i
            r['events'] = []

    pages = term_pages(term)
    print(f"Term {term} | {len(pages)} calendar pages")
    session = requests.Session()
    session.headers.update(HEADERS)

    def get(path):
        try:
            return session.get(BASE + path, timeout=30).text
        except requests.RequestException as e:
            print(f"  failed {path}: {e}", file=sys.stderr)
            return ''

    def get_feed(item):
        org, url = item
        for attempt in range(3):
            try:
                r = session.get(url, timeout=45)
                r.raise_for_status()
                return list(parse_ics(r.text, org))
            except requests.RequestException as e:
                err = e
                time.sleep(2 * (attempt + 1))
        print(f"  failed {org}: {err}", file=sys.stderr)
        return []

    with ThreadPoolExecutor(max_workers=6) as pool:
        htmls = list(pool.map(get, pages))
        feeds = list(pool.map(get_feed, ICAL_FEEDS.items()))

    now = datetime.now(LA)
    lo, hi = now - timedelta(days=KEEP_PAST_DAYS), now + timedelta(days=KEEP_FUTURE_DAYS)
    all_events = [ev for html in htmls for ev in parse_events(html)]
    all_events += [ev for feed in feeds for ev in feed if lo <= ev['start'] <= hi]
    print(f"{len(ICAL_FEEDS)} department feeds, {sum(map(len, feeds))} feed events")

    seen, dup, matched = set(), set(), 0
    for ev in all_events:
        if ev['id'] in seen:
            continue
        seen.add(ev['id'])
        for idx in match_rooms(ev['location'], room_index):
            # The same talk is often cross-listed on several department feeds.
            key = (idx, ev['start'], ev['title'].lower())
            if key in dup:
                continue
            dup.add(key)
            rooms[idx]['events'].append({
                'title': ev['title'],
                'org': ev['org'],
                'url': ev['url'],
                'date': ev['start'].strftime('%Y-%m-%d'),
                'start_time': ev['start'].strftime('%I:%M %p'),
                'end_time': ev['stop'].strftime('%I:%M %p') if ev['stop'].date() == ev['start'].date() else '11:59 PM',
            })
            matched += 1
            print(f"  {rooms[idx]['text']}: {ev['start']:%a %m/%d %I:%M %p} {ev['title']}")

    for r in rooms:
        if r.get('events'):
            r['events'].sort(key=lambda e: (e['date'], datetime.strptime(e['start_time'], '%I:%M %p')))

    with open(args.file, 'w') as f:
        json.dump(rooms, f, indent=1)
        f.write('\n')
    print(f"{len(seen)} events scanned | {matched} matched to classrooms")


if __name__ == '__main__':
    main()
