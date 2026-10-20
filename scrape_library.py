"""
Scrape UCLA libraries, library study rooms and computer labs into library.json.

The output has the same shape as hill.json, so the page can reuse the same
code: every entry is a room with open hours and free time per date.

    rooms: [{text, building, room, space, description, capacity, type,
             characteristics, url, area,
             days: {YYYY-MM-DD: {free: [[start, end], ...], hours: [open, close],
                                 closed: [[start, end], ...],      # optional
                                 booked: [[start, end, label], ...]}}}]  # optional

Minutes are minutes after midnight, campus time. Inside [open, close], time
that isn't in `free` is taken: by a reservation, or by the named booking in
`booked`. `closed` marks gaps inside the day when the place is shut (Powell is
open late, closes at 5 am, then opens again at 8 am). A day with hours null is
closed all day.

Sources:
- Library hours: UCLA Library's LibCal hours feed
  (calendar.library.ucla.edu/api_hours_grid.php, institution 3244).
- Library study rooms: LibCal spaces (calendar.library.ucla.edu/spaces). The
  availability grid lists every bookable 30 minute slot in the booking window;
  booked slots carry a class name. Beyond the booking window nobody can book
  yet, so the room counts as free during library hours.
- CLICC classrooms: open for drop-in study when not booked. Bookings come from
  the LibCal calendar that the CLICC Classrooms page links to.
- SEASnet computer labs (Boelter Hall): hours from seasnet.ucla.edu. Classes
  held in 3760 and 4404 come from the registrar schedule in classrooms.json
  (scrape.py fetches those two rooms daily).

About 17 requests per run, one at a time.

Usage:
    python scrape_library.py [--days 8] [--file library.json]
"""

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from scrape import HEADERS

LIBCAL = 'https://calendar.library.ucla.edu'
HOURS_URL = LIBCAL + '/api_hours_grid.php?iid=3244&format=json&weeks=2'
LA = ZoneInfo('America/Los_Angeles')
LOC = 'https://www.library.ucla.edu/visit/locations/'
DAY = 24 * 60

# Whole libraries: one entry each, open whenever LibCal says so.
# (hours lid, text, building, room, capacity, description, url)
LIBRARIES = [
    (2572, 'Powell Library', 'POWELL', 'Library', None,
     'Main undergraduate library. Reading rooms on the first and second floors.', LOC + 'powell-library/'),
    (4699, 'Night Powell', 'POWELL', 'First floor study hall', None,
     'Overnight study in the Powell first floor study hall for current UCLA students, faculty and staff. '
     'Physical BruinCard needed.', LOC + 'powell-library/'),
    (1916, 'Young Research Library', 'YRL', 'Library', None,
     'Research library. The first floor Reading Room and Research Commons are open to all.', LOC + 'research-library/'),
    (4693, 'East Asian Library', 'YRL', '21617', None,
     'Second floor of the Young Research Library.', LOC + 'east-asian-library/'),
    (2081, 'Biomedical Library', 'HLTHSCI', '12-077', None,
     'Louise M. Darling Biomedical Library, first floor of the Center for the Health Sciences.', LOC + 'biomed/'),
    (2082, 'Biomedical Study Commons', 'HLTHSCI', 'Study Commons', None,
     'Open to UCLA students, faculty and staff during library hours. Overnight only for graduate students '
     'in Medicine, Dentistry, Nursing, Public Health and Life Sciences.', LOC + 'biomed/'),
    (4702, 'Science and Engineering Library (Boelter)', 'BOELTER', '8270', None,
     'SEL/Boelter, 8270 Boelter Hall, via the northeast elevator or stairs.', LOC + 'science-engineering-library/'),
    (4703, 'Science and Engineering Library (Geology)', 'GEOLOGY', '4697', None,
     'SEL/Geology, 4697 Geology Building. Quiet reading room.', LOC + 'science-engineering-library/'),
    (4690, 'Arts Library', 'PUB AFF', '1400', None,
     'First floor of the Public Affairs (Luskin) building.', LOC + 'arts-library/'),
    (4696, 'Music Library', 'SMB', '1102', None,
     'Walter H. Rubsamen Music Library, off the Schoenberg Music Building entrance atrium.', LOC + 'music-library/'),
    (3280, 'Rosenfeld Management Library', 'ROSNFLD', 'Library', None,
     'Anderson School of Management, E building. Open to UCLA Anderson and the public.',
     LOC + 'eugene-and-maxine-rosenfeld-management-library/'),
    (4694, 'Law Library', 'LAW', '1112', None,
     'Hugh & Hazel Darling Law Library. Limited access: non-law UCLA students need a same day reservation '
     'and a BruinCard.', 'https://law.ucla.edu/library/access-policies'),
]

# LibCal space locations, and the library hours that apply past the booking window.
SPACE_LOCATIONS = {4361: 2572, 5567: 1916, 4752: 4696, 6578: 2081, 8312: 4702, 19391: 22369}

CLICC = ('Current UCLA students, faculty and staff can book up to 3 hours, 3 days ahead. '
         'Drop-in study when not reserved.')
# Room groups we show, by LibCal group id: building, room number and display
# name from the LibCal title, description, and which library hours apply.
SPACE_GROUPS = {
    7748: dict(building='POWELL', room=lambda t: t.replace('Powell Group Study Room ', 'Study Room '),
               desc='By the first floor reading room in Powell. Whiteboard and screen. ' + CLICC),
    35623: dict(building='POWELL', room=lambda t: '145 ' + t.replace(' (CLICC)', ''),
                text=lambda t: 'Powell CLICC ' + t.replace(' (CLICC)', ''),
                desc='Individual booth in the CLICC Lab (Powell 145). ' + CLICC),
    7750: dict(building='YRL', room=lambda t: t.replace('YRL Collaboration Pod ', 'Pod '),
               desc='YRL Research Commons, first floor. LCD screen. ' + CLICC),
    7749: dict(building='YRL', room=lambda t: t.replace('YRL Group Study Room ', 'Study Room '),
               desc='YRL Research Commons, first floor. ' + CLICC),
    14403: dict(building='SMB', room=lambda t: t.replace('Music Library ', ''),
                desc='Music Library, 1102 Schoenberg Music Building.'),
    11674: dict(building='HLTHSCI', room=lambda t: t.split()[0], text=lambda t: 'Biomedical Library ' + t,
                desc='Biomedical Library first floor. For groups of two or more.'),
    14014: dict(building='HLTHSCI', room=lambda t: t.replace('Collaboration Study Room ', 'Collab Room '),
                text=lambda t: 'Biomedical ' + t, hours=24794,
                desc='Biomedical Library Collaboration Hub, sixth floor of CHS (22-001). Groups of two or more; '
                     'check in at the service desk.'),
    53757: dict(building='HLTHSCI', room=lambda t: t.replace('Biomedical Study Commons Room ', ''), hours=2082,
                desc='Biomedical Study Commons. Open to BruinCard holders during library hours.'),
    14408: dict(building=None, room=lambda t: t.split(' - ')[0].split()[-1],
                text=lambda t: 'SEL Group Study Room ' + t.split(' - ')[0],
                desc='Science and Engineering Library. Groups of two or more, up to 2 hours, 7 days ahead.'),
    40875: dict(building='POWELL', room=lambda t: '270 ' + t, text=lambda t: 'Powell Media Lab ' + t,
                desc='Instructional Media Lab, Powell 270. Quiet study; check in at the front desk. '
                     'Up to 2 hours, 7 days ahead.'),
}
SEL_GEOLOGY_HOURS = 4703

# CLICC classrooms: LibCal calendar id -> (room, name, seats). Hours: "CLICC Classrooms Hub".
CLICC_HOURS = 2607
CLICC_CLASSROOMS = {
    3363: ('POWELL', '307', 'CLICC Classroom A', 25),
    4357: ('POWELL', '320B', 'CLICC Classroom B', 26),
    4358: ('POWELL', '320', 'CLICC Classroom C', 32),
    4759: ('YRL', '11630F', 'CLICC RC Classroom', 26),
}
CLICC_PAGE = 'https://www.library.ucla.edu/help/services-resources/clicc-classrooms/'

# CLICC Lab in Powell: "Computer Lab (CLICC)" on the Powell location page.
CLICC_LAB_HOURS = 2609

SEASNET_URL = 'https://www.seasnet.ucla.edu/seasnet-computer-labs/'
# Room -> workstations, from the SEASnet labs page.
SEASNET_LABS = {'2664': 53, '3760': 29, '4404': 65, '4405': 16, '4442': 36}
# Labs that also hold classes; their registrar schedule is in classrooms.json.
LAB_REGISTRAR_ROOMS = {('BOELTER', '3760'), ('BOELTER', '4404')}

WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


class ScrapeError(Exception):
    pass


def clock(s):
    """'8am' / '9:30pm' / '12am' -> minutes after midnight."""
    m = re.fullmatch(r'\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*', s.lower())
    if not m:
        raise ValueError(s)
    h = int(m.group(1)) % 12 + (12 if m.group(3) == 'p' else 0)
    return h * 60 + int(m.group(2) or 0)


def merge(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def subtract(ranges, cuts):
    out = []
    for a, b in ranges:
        cur = a
        for c, d in sorted(cuts):
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append([cur, c])
            cur = max(cur, d)
        if cur < b:
            out.append([cur, b])
    return out


def day_record(open_ranges, booked=()):
    """Open ranges + named bookings -> one day of the hill.json format."""
    open_ranges = merge(open_ranges)
    if not open_ranges:
        return {'free': [], 'hours': None, 'closed': [[0, DAY]]}
    rec = {'free': subtract(open_ranges, [b[:2] for b in booked]),
           'hours': [open_ranges[0][0], open_ranges[-1][1]]}
    gaps = [[a[1], b[0]] for a, b in zip(open_ranges, open_ranges[1:])]
    if gaps:
        rec['closed'] = gaps
    inside = [list(b) for b in booked if b[0] < rec['hours'][1] and b[1] > rec['hours'][0]]
    if inside:
        rec['booked'] = sorted(inside)
    return rec


def library_hours(session, dates):
    """lid -> {date: [[open, close], ...]} for the dates we want. Missing date = unknown."""
    data = session.get(HOURS_URL, timeout=30).json()
    want = set(dates)
    out = {}
    for loc in data['locations']:
        days = out.setdefault(loc['lid'], {})
        for week in loc['weeks']:
            for info in week.values():
                d, t = info['date'], info['times']
                nxt = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
                if t['status'] == 'closed':
                    days.setdefault(d, [])
                elif t['status'] == '24hours':
                    days.setdefault(d, []).append([0, DAY])
                elif t['status'] == 'open':
                    for h in t.get('hours', []):
                        a, b = clock(h['from']), clock(h['to'])
                        if b <= a:  # past midnight: "10pm - 5am", "6pm - 12am"
                            days.setdefault(d, []).append([a, DAY])
                            if b:
                                days.setdefault(nxt, []).append([0, b])
                        else:
                            days.setdefault(d, []).append([a, b])
                # 'not-set' and 'text' days stay unknown
    # A closing time past midnight lands on the next day even if that day is
    # otherwise unknown; only keep the dates we asked for.
    return {lid: {d: r for d, r in days.items() if d in want} for lid, days in out.items()}


def spaces_for(session, lid):
    """Resources listed on the public spaces page: eid -> (title, gid, capacity)."""
    html = session.get(f"{LIBCAL}/spaces?lid={lid}&gid=0", timeout=30).text
    out = {}
    for m in re.finditer(r'resources\.push\(\{(.*?)\}\);', html, re.S):
        block = m.group(1)
        eid = int(re.search(r'eid:\s*(\d+)', block).group(1))
        title = json.loads(re.search(r'title:\s*("(?:[^"\\]|\\.)*")', block).group(1))
        gid = int(re.search(r'gid:\s*(\d+)', block).group(1))
        cap = re.search(r'capacity:\s*(\d+)', block)
        out[eid] = (re.sub(r'\s*\(Capacity \d+\)\s*$', '', title).strip(), gid, int(cap.group(1)) if cap else None)
    return out


def grid_for(session, lid, start, end):
    """Availability grid: list of slots {start, end, itemId, className?}."""
    r = session.post(f"{LIBCAL}/spaces/availability/grid", timeout=30, data={
        'lid': lid, 'gid': 0, 'eid': -1, 'seat': 0, 'seatId': 0, 'zone': 0,
        'start': start, 'end': end, 'pageIndex': 0, 'pageSize': 100,
    }, headers={'Referer': f"{LIBCAL}/spaces?lid={lid}", 'X-Requested-With': 'XMLHttpRequest'})
    r.raise_for_status()
    return r.json().get('slots', [])


def minutes(ts):
    """'2026-10-13 08:30:00' -> ('2026-10-13', 510)."""
    d, t = ts.split(' ')
    h, m, _ = t.split(':')
    return d, int(h) * 60 + int(m)


def study_rooms(session, dates, hours, failed):
    rooms = []
    end = (date.fromisoformat(dates[-1]) + timedelta(days=1)).isoformat()
    now = datetime.now(LA)
    now_min = now.hour * 60 + now.minute
    for lid, default_hours in SPACE_LOCATIONS.items():
        try:
            spaces = spaces_for(session, lid)
            time.sleep(0.5)
            slots = grid_for(session, lid, dates[0], end)
            time.sleep(0.5)
        except (requests.RequestException, ValueError, AttributeError) as e:
            failed.append(f"spaces {lid}: {e}")
            continue
        by_item = {}
        dates_with_slots = set()
        for s in slots:
            d, a = minutes(s['start'])
            d2, b = minutes(s['end'])
            if d2 != d:
                b = DAY
            dates_with_slots.add(d)
            by_item.setdefault(s['itemId'], {}).setdefault(d, []).append((a, b, not s.get('className')))
        for eid, (title, gid, cap) in spaces.items():
            g = SPACE_GROUPS.get(gid)
            if not g:
                continue
            building = g['building']
            if building is None:  # SEL titles name the building: "Boelter 8251A - ..."
                building = 'GEOLOGY' if title.startswith('Geology') else 'BOELTER'
            lib = hours.get(SEL_GEOLOGY_HOURS if building == 'GEOLOGY' else g.get('hours', default_hours), {})
            days = {}
            for i, d in enumerate(dates):
                item_slots = by_item.get(eid, {}).get(d)
                # LibCal leaves out slots that already started today. Count the
                # past part of today's library hours as open so the calendar
                # doesn't show it as closed; it has no effect on "free now".
                past = [[a, min(b, now_min)] for a, b in lib.get(d, []) if a < now_min] if i == 0 else []
                if item_slots:
                    # The slot in progress is left out too: count it as taken.
                    first = min(a for a, _, _ in item_slots)
                    current = [[now_min, first]] if past and now_min < first else []
                    bookable = merge([[a, b] for a, b, _ in item_slots] + past + current)
                    free = merge([[a, b] for a, b, ok in item_slots if ok] + past)
                    rec = day_record(bookable)
                    rec['free'] = free
                    days[d] = rec
                elif d in dates_with_slots:
                    days[d] = day_record(past)  # bookable elsewhere that day but not here: closed
                elif d in lib:
                    days[d] = day_record(lib[d])  # past the booking window: nobody can book it yet
            rooms.append({
                'text': g['text'](title) if 'text' in g else title,
                'building': building,
                'room': g['room'](title),
                'space': title,
                'description': g['desc'],
                'capacity': cap,
                'type': 'Library study room',
                'characteristics': [],
                'url': f"{LIBCAL}/space/{eid}",
                'area': 'library',
                'days': days,
            })
    return rooms


def clicc_bookings(session, dates):
    """calendar id -> {date: [[start, end, title], ...]} from the CLICC classrooms calendar."""
    cals = ','.join(map(str, CLICC_CLASSROOMS))
    url = f"{LIBCAL}/ajax/calendar/list?c={cals}&date=0000-00-00&perpage=100&page=1&audience=&cats=&camps=&inc=0"
    data = session.get(url, timeout=30, headers={'X-Requested-With': 'XMLHttpRequest'}).json()
    out = {}
    for e in data.get('results', []):
        cid = int(e.get('calendar_id') or 0)
        if cid not in CLICC_CLASSROOMS or e.get('all_day'):
            continue
        d, a = minutes(e['startdt'])
        d2, b = minutes(e['enddt'])
        if d2 != d:
            b = DAY
        if d in dates:
            out.setdefault(cid, {}).setdefault(d, []).append([a, b, ' '.join(e['title'].split())])
    return out


def clicc_rooms(session, dates, hours, failed):
    rooms = []
    lab_hours = hours.get(CLICC_LAB_HOURS, {})
    if lab_hours:
        rooms.append({
            'text': 'CLICC Lab (Powell 145)', 'building': 'POWELL', 'room': '145', 'space': 'CLICC Lab',
            'description': 'Computer Lab (CLICC) in Powell: a large drop-in space for collaborative study with '
                           'outlets and wepa printing. Current UCLA students, faculty and staff; show your BruinCard.',
            'capacity': None, 'type': 'Computer lab', 'characteristics': [],
            'url': LOC + 'powell-library/', 'area': 'library',
            'days': {d: day_record(lab_hours[d]) for d in dates if d in lab_hours},
        })
    hub = hours.get(CLICC_HOURS, {})
    try:
        booked = clicc_bookings(session, dates)
    except (requests.RequestException, ValueError) as e:
        failed.append(f"CLICC calendar: {e}")
        return rooms  # without bookings a classroom would look free when it isn't
    for cid, (building, room, name, seats) in CLICC_CLASSROOMS.items():
        rooms.append({
            'text': f"{name} ({building.title() if building == 'POWELL' else building} {room})",
            'building': building, 'room': room, 'space': name,
            'description': 'Technology classroom, open for drop-in study when not booked.',
            'capacity': seats, 'type': 'Library classroom', 'characteristics': [],
            'url': CLICC_PAGE, 'area': 'library',
            'days': {d: day_record(hub[d], booked.get(cid, {}).get(d, [])) for d in dates if d in hub},
        })
    return rooms


def seasnet_hours(session):
    """Weekday -> [open, close] from the SEASnet labs page, or None if it can't be read."""
    text = re.sub(r'<[^>]+>', ' ', session.get(SEASNET_URL, timeout=30).text)
    text = ' '.join(text.replace('&#8211;', '-').replace('–', '-').split())
    out = {}
    for day in WEEKDAYS:
        m = re.search(rf'{day}:\s*(\d{{1,2}}(?::\d\d)?\s*[ap]m)\s*-\s*(\d{{1,2}}(?::\d\d)?\s*[ap]m)', text, re.I)
        if not m:
            m_closed = re.search(rf'{day}:\s*Closed', text, re.I)
            if not m_closed:
                return None
            out[day] = None
            continue
        out[day] = [clock(m.group(1)), clock(m.group(2))]
    return out


def class_blocks(schedule, day):
    out = []
    for c in (schedule or {}).get(day, []):
        try:
            a = datetime.strptime(c['start_time'], '%I:%M %p')
            b = datetime.strptime(c['end_time'], '%I:%M %p') if c.get('end_time') else a + timedelta(minutes=50)
        except ValueError:
            continue
        out.append([a.hour * 60 + a.minute, b.hour * 60 + b.minute, f"{c['course']} {c.get('type', '')}".strip()])
    return out


def seasnet_labs(session, dates, failed, classrooms_file='classrooms.json'):
    try:
        weekly = seasnet_hours(session)
    except requests.RequestException as e:
        failed.append(f"SEASnet: {e}")
        return []
    if not weekly:
        failed.append('SEASnet: could not read lab hours')
        return []
    schedules = {}
    try:
        for r in json.load(open(classrooms_file)):
            key = (r.get('building'), str(r.get('room', '')).lstrip('0'))
            if key in LAB_REGISTRAR_ROOMS:
                schedules[key[1]] = r.get('schedule')
    except (OSError, ValueError):
        pass
    rooms = []
    for room, seats in SEASNET_LABS.items():
        days = {}
        for d in dates:
            wd = WEEKDAYS[date.fromisoformat(d).weekday()]
            h = weekly[wd]
            days[d] = day_record([h] if h else [], class_blocks(schedules.get(room), wd))
        rooms.append({
            'text': f"SEASnet Lab (Boelter {room})", 'building': 'BOELTER', 'room': room, 'space': 'SEASnet',
            'description': 'Windows lab for engineering coursework. Needs a SEASnet account (engineering students, '
                           'or anyone enrolled in an engineering course). Closed on university holidays.'
                           + (' Classes are held here at times.' if ('BOELTER', room) in LAB_REGISTRAR_ROOMS else ''),
            'capacity': seats, 'type': 'Computer lab', 'characteristics': [],
            'url': SEASNET_URL, 'area': 'library', 'days': days,
        })
    return rooms


def collect(days=8):
    session = requests.Session()
    session.headers.update(HEADERS)
    today = datetime.now(LA).date()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(days)]
    failed = []
    try:
        hours = library_hours(session, dates)
    except (requests.RequestException, ValueError, KeyError) as e:
        raise ScrapeError(f"library hours: {e}")
    rooms = []
    for lid, text, building, room, cap, desc, url in LIBRARIES:
        if lid not in hours:
            failed.append(f"hours {lid} missing")
            continue
        rooms.append({
            'text': text, 'building': building, 'room': room, 'space': text, 'description': desc,
            'capacity': cap, 'type': 'Library', 'characteristics': [], 'url': url, 'area': 'library',
            'days': {d: day_record(hours[lid][d]) for d in dates if d in hours[lid]},
        })
    rooms += study_rooms(session, dates, hours, failed)
    rooms += clicc_rooms(session, dates, hours, failed)
    rooms += seasnet_labs(session, dates, failed)
    return {
        'updated': datetime.now(LA).isoformat(timespec='seconds'),
        'source': LIBCAL,
        'failed': failed,
        'dates': dates,
        'rooms': rooms,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=8)
    ap.add_argument('--file', default='library.json')
    args = ap.parse_args()
    try:
        out = collect(args.days)
    except ScrapeError as e:
        sys.exit(f"ERROR: {e}, not saving")
    if len(out['failed']) > 4:
        sys.exit(f"ERROR: too many failures ({out['failed']}), not saving")
    with open(args.file, 'w') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    kinds = {}
    for r in out['rooms']:
        kinds[r['type']] = kinds.get(r['type'], 0) + 1
    print(f"{len(out['rooms'])} entries {kinds}, {args.days} days, failed: {out['failed'] or 'none'}")


if __name__ == '__main__':
    main()
