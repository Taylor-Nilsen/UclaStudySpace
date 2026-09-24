"""
Pull dated non-class events (clubs, departments, workshops) held in our
classrooms from UCLA Community (community.ucla.edu) and attach them to
classrooms.json as room['events'].

The registrar grids only show academic classes. Room reservations made through
the UCLA Events Office are not published anywhere public, so this is the only
open source of non-class bookings. It catches the events that orgs post with a
room number in the location.

Usage:
    python scrape_events.py [--term 26F]
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from scrape import HEADERS, current_term

BASE = 'https://community.ucla.edu'
LA = ZoneInfo('America/Los_Angeles')
SEASONS = {'W': 'winter', 'S': 'spring', 'U': 'summer', 'F': 'fall'}

# Building code in classrooms.json -> regex for how people write it.
BUILDING_ALIASES = {
    'BOELTER': r'boelter',
    'BUNCHE': r'bunche',
    'PUB AFF': r'public\s+affairs|pub\.?\s*aff',
    'MS': r'math(?:ematical|\.)?\s*sci(?:ences?|\.)?(?:\s+building)?|\bMS\b',
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


def norm_room(room):
    """'02444' -> '2444', 'A00214' -> 'A214', 'CS 24' -> 'CS24'."""
    m = re.match(r'^\s*([A-Z]*)\s*0*(\d+\w*)\s*$', room, re.I)
    return (m.group(1) + m.group(2)).upper() if m else room.strip().upper()


def match_rooms(location, room_index):
    """Return classroom indexes the free-text location points at."""
    hits = []
    for code, rx in BUILDING_RES.items():
        m = rx.search(location)
        if not m:
            continue
        rooms = room_index.get(code, {})
        rest = location[m.end():]
        for prefix, num in ROOM_TOKEN_RE.findall(rest):
            key = (prefix + num).upper()
            if key in rooms:
                hits.append(rooms[key])
            elif num.upper() in rooms:
                hits.append(rooms[num.upper()])
    return hits


def term_pages(term):
    slug = f"/term/{SEASONS[term[2]]}-20{term[:2]}"
    html = requests.get(BASE + '/calendars', headers=HEADERS, timeout=30).text
    links = set(re.findall(r'href="(%s/[^"]+)"' % re.escape(slug), html))
    if not links:  # past terms drop off the index page; guess the week URLs
        links = {f"{slug}/week-{i}" for i in range(11)} | {f"{slug}/finals-week"}
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

    with ThreadPoolExecutor(max_workers=6) as pool:
        htmls = list(pool.map(get, pages))

    seen, matched = set(), 0
    for html in htmls:
        for ev in parse_events(html):
            if ev['id'] in seen:
                continue
            seen.add(ev['id'])
            for idx in match_rooms(ev['location'], room_index):
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
