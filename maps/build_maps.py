#!/usr/bin/env python3
"""Build the building and campus map data in maps/ from public sources.

    python maps/build_maps.py                   # use cached downloads when present
    python maps/build_maps.py --refresh         # download everything again
    python maps/build_maps.py --cache /tmp/x    # where raw downloads go

Raw downloads (OpenStreetMap XML, UCLA GIS JSON, the accessibility PDF,
Concept3D JSON) go to the cache directory, which is git-ignored. Only the
small per-building JSON files are committed.

Sources, in the order they are trusted:
  1. OpenStreetMap API 0.6 (building outlines, doors tagged entrance=*,
     steps, elevators, toilets).
  2. UCLA's own map data:
     - map.ucla.edu ArcGIS: building outlines (location areas), floor counts,
       gender inclusive restrooms with room, floor and position.
     - UCLA's Concept3D campus map (maps.ucla.edu): surveyed doors (powered
       and unpowered), outdoor elevators, accessible restrooms.
     - UCLA Campus Accessibility Map PDF (Oct 2020): elevator dots. The PDF is
       georeferenced against OSM building outlines, so these are approximate.
  3. UCLA room numbering (first digit = floor), checked per building against
     UCLA's restroom records that list both a room and a floor.

Nothing here is guessed: if a source doesn't place something, it isn't drawn.
Needs: Python 3.9+, pdfplumber (pip install pdfplumber) for the PDF step.
"""
import argparse
import datetime
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import floorplans as FP  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
UA = 'UclaStudySpace-maps/1.0 (+https://github.com/Taylor-Nilsen/UclaStudySpace)'

# Campus + the Hill + Southwest apartments.
BBOX = (-118.457, 34.060, -118.435, 34.079)
TILE = (0.004, 0.003)

OSM_API = 'https://www.openstreetmap.org/api/0.6/map?bbox={},{},{},{}'
ARCGIS = 'https://map.ucla.edu/arcgis/rest/services/'
C3D_KEY = '0001085cc708b9cef47080f064612ca5'   # public key the maps.ucla.edu web app uses
C3D = 'https://api.concept3d.com/'
ACCESS_PDF = 'https://map.ucla.edu/downloads/pdf/Access_Oct_2020.pdf'

# Concept3D categories on UCLA's map (id 2043).
C3D_POWERED, C3D_UNPOWERED, C3D_ELEVATOR, C3D_RESTROOM = 80910, 81248, 87090, 92079

# ---------------------------------------------------------------------------
# Buildings with rooms on the site.
#   ucla: names used by UCLA GIS / Concept3D / restroom records
#   osm:  OSM building names (optional; otherwise matched by outline overlap)
#   wing_prefixes: letter prefixes on room numbers that name a wing, not a floor
#                  (UCLA lists Olympic "OH107" on floor 1)
#   level_first_letter: two-letter prefixes start with the floor (CHS "AL213" is on A)
#   aliases: other codes that should open this building's map
# ---------------------------------------------------------------------------
B = {
    'BOELTER': dict(name='Boelter Hall', ucla=['Boelter Hall', 'Boelter'], osm=['Boelter Hall'],
                    aliases=['SEL', 'SEL BOELTER', 'SCI ENG LIB', 'SEL/EMS', 'BOELTR']),
    'BROAD': dict(name='Broad Art Center', ucla=['Broad Art Center', 'Broad'], osm=['Broad Art Center']),
    'BUNCHE': dict(name='Bunche Hall', ucla=['Bunche Hall', 'Bunche'], osm=['Bunche Hall']),
    'DODD': dict(name='Dodd Hall', ucla=['Dodd Hall', 'Dodd'], osm=['Dodd Hall']),
    'FOWLER': dict(name='Fowler Museum', ucla=['Fowler Museum', 'Fowler'], osm=['Fowler Museum']),
    'FRANZ': dict(name='Franz Hall', ucla=['Franz Hall', 'Franz'], osm=['Franz Hall']),
    'GEOLOGY': dict(name='Geology Building', ucla=['Geology', 'Geology Building'], osm=[], aliases=['SEL GEOLOGY', 'SEL/GEOLOGY', 'GEOL']),
    'HAINES': dict(name='Haines Hall', ucla=['Haines Hall', 'Haines'], osm=['Haines Hall']),
    'KAPLAN': dict(name='Kaplan Hall', ucla=['Kaplan Hall', 'Kaplan'], osm=['Kaplan Hall']),
    'KAUFMAN': dict(name='Kaufman Hall', ucla=['Kaufman Hall', 'Kaufman'], osm=['Kaufman Hall']),
    'KNSY PV': dict(name='Kinsey Teaching Pavilion', ucla=['Kinsey Pavilion', 'Kinsey Teaching Pavilion', 'Kinsey Science Teaching Pavilion'],
                    osm=['Kinsey Teaching Pavilion']),
    'LAKRETZ': dict(name='La Kretz Hall', ucla=['La Kretz Hall', 'LaKretz Hall'], osm=['La Kretz Hall']),
    'MOORE': dict(name='Moore Hall', ucla=['Moore Hall', 'Moore'], osm=['Moore Hall']),
    'MS': dict(name='Mathematical Sciences', ucla=['Mathematical Sciences', 'Mathematical Sciences Building', 'Math Sciences'],
               osm=['Math Sciences']),
    'PAB': dict(name='Physics and Astronomy Building', ucla=['Physics and Astronomy Building', 'Physics And Astronomy Building', 'PAB'],
                osm=['Physics & Astronomy Building']),
    'PERLOFF': dict(name='Perloff Hall', ucla=['Perloff Hall', 'Perloff'], osm=['Perloff Hall']),
    # UCLA Library's 2026 locations map lists the Arts Library at 1400 Public Affairs
    'PUB AFF': dict(name='Public Affairs Building', ucla=['Public Affairs Building', 'Public Affairs'], osm=['Public Affairs Building'],
                    aliases=['ARTS', 'ARTS LIB', 'ARTSLIB', 'ARTS LIBRARY', 'PUBAFF']),
    'ROLFE': dict(name='Rolfe Hall', ucla=['Rolfe Hall', 'Rolfe'], osm=['Rolfe Hall']),
    'ROYCE': dict(name='Royce Hall', ucla=['Royce Hall', 'Royce'], osm=['Royce Hall']),
    'SLICHTR': dict(name='Slichter Hall', ucla=['Slichter Hall', 'Slichter'], osm=[]),
    'WGYOUNG': dict(name='Young Hall', ucla=['Young Hall'], osm=['Young Hall']),
    # the Hill
    'COVEL': dict(name='Covel Commons', ucla=['Covel Commons', 'Covel'], osm=['Covel Commons (Sunset Village)'], hill=True),
    'DE NEVE': dict(name='De Neve Commons', ucla=['De Neve Commons'], osm=['De Neve Commons Building'], hill=True),
    'CARNESL': dict(name='Carnesale Commons', ucla=['Carnesale Commons'], osm=[], hill=True),
    'HEDRICK': dict(name='Hedrick Hall', ucla=['Hedrick Hall', 'Hedrick'], osm=['Hedrick Hall'], hill=True),
    'RIEBER': dict(name='Rieber Hall', ucla=['Rieber Hall', 'Rieber'], osm=['Rieber Hall'], hill=True),
    'SPROUL': dict(name='Sproul Hall', ucla=['Sproul Hall', 'Sproul'], osm=['Sproul Hall'], hill=True),
    'HITCH': dict(name='Hitch Residential Suites', ucla=['Hitch Suites', 'Hitch A', 'Hitch B', 'Hitch C', 'Hitch D', 'Hitch Commons'],
                  osm=['Hitch Residential Suites'], hill=True),
    'OLYMPIC': dict(name='Olympic Hall', ucla=['Olympic Hall'], osm=['Olympic Hall'], hill=True, wing_prefixes=['OH']),
    'CNYN PT': dict(name='Canyon Point', ucla=['Canyon Point'], osm=['Canyon Point'], hill=True),
    'GAYLEY HEIGHTS': dict(name='Gayley Heights', ucla=['Gayley Heights Apartments', 'Gayley Heights'], osm=['Gayley Heights'], hill=True),
    'TIPUANA': dict(name='Tipuana (Southwest Campus Apartments)', ucla=['Tipuana'], osm=[], hill=True),
    'LAUREL': dict(name='Laurel (Southwest Campus Apartments)', ucla=['Laurel'], osm=[], hill=True),
    'PALOS VERDE': dict(name='Palo Verde (Southwest Campus Apartments)', ucla=['Palo Verde', 'Palos Verde'], osm=[], hill=True,
                        aliases=['PALO VERDE']),
    # libraries (rooms added by the library/lab scraper)
    'POWELL': dict(name='Powell Library', ucla=['Powell Library', 'Powell'], osm=['Powell Library'],
                   aliases=['POWELL LIB', 'POWELL LIBRARY', 'PWL']),
    'YRL': dict(name='Charles E. Young Research Library', ucla=['Young Research Library', 'Charles E. Young Research Library'],
                osm=['Charles E. Young Research Library'], aliases=['YRL LIB', 'YOUNG RESEARCH LIBRARY', 'RESEARCH LIB']),
    'CHS': dict(name='Center for Health Sciences (Biomedical Library)', ucla=['Center for Health Sciences'],
                osm=['Center for Health Sciences'], level_first_letter=True, aliases=['BIOMED', 'BIOMED LIB', 'BIOMEDLIB', 'BIOMEDICAL LIBRARY', 'LS BIOMED', 'HLTHSCI']),
    'SCHOENBG': dict(name='Schoenberg Music Building (Music Library)', ucla=['Schoenberg Music Building', 'Schoenberg Hall'],
                     osm=['Schoenberg Music Building'], aliases=['SCHOENBERG', 'SCHNBRG', 'MUSIC', 'MUSIC LIB', 'MUSICLIB', 'MUSIC LIBRARY', 'SMB']),
    'ROSENFLD': dict(name='Rosenfeld Library (Anderson School)', ucla=['Rosenfeld Library'], osm=['Rosenfeld Library'],
                     aliases=['ROSENFELD', 'ROSENFELD LIB', 'MGMT LIB', 'MANAGEMENT LIBRARY', 'ANDERSON', 'ROSNFLD']),
}

# Letters UCLA uses as floor names (its restroom records list floors "A", "B", "C", "M").
LEVEL_LETTERS = {'A', 'B', 'C', 'M'}


def floor_of(room, wing_prefixes=(), level_first_letter=False):
    """Floor from a UCLA room number. Mirrors floorOf() in maps.js.

    '02444' -> '2', 'A00152' -> 'A', '00078' -> '0', '12-077' -> '1'.
    wing_prefixes: letters that name a wing, not a floor ('OH107' in Olympic -> '1').
    level_first_letter: two-letter prefixes start with the floor ('AL213' in CHS -> 'A').
    Returns None when the floor can't be read from the number (e.g. Young Hall 'CS24').
    """
    r = re.sub(r'[\s-]+', '', str(room or '').upper())
    for w in sorted(wing_prefixes, key=len, reverse=True):
        if r.startswith(w) and len(r) > len(w):
            r = r[len(w):]
            break
    m = re.match(r'^([A-Z]*)0*(\d+)[A-Z]?$', r)
    if not m:
        return None
    prefix, digits = m.group(1), m.group(2)
    if prefix:
        if prefix in LEVEL_LETTERS:
            return prefix
        if level_first_letter and prefix[0] in LEVEL_LETTERS:
            return prefix[0]
        return None
    if len(digits) <= 2:
        return '0'
    return digits[0]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
LAT0, LON0 = 34.0700, -118.4450
KX = math.cos(math.radians(LAT0)) * 111320.0
KY = 110540.0


def proj(lat, lon):
    return ((lon - LON0) * KX, (lat - LAT0) * KY)


def unproj(x, y):
    return (LAT0 + y / KY, LON0 + x / KX)


def area_centroid(pts):
    a = cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        a += f
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    if abs(a) < 1e-9:
        return 0.0, pts[0]
    return a / 2, (cx / (3 * a), cy / (3 * a))


def pip(pt, poly):
    x, y = pt
    inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def in_shape(pt, shape):
    """shape = list of (outer, [holes]) in projected metres."""
    return any(pip(pt, o) and not any(pip(pt, h) for h in holes) for o, holes in shape)


def seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def edge_dist(pt, shape):
    best = 1e9
    for o, holes in shape:
        for ring in [o] + holes:
            for i in range(len(ring)):
                best = min(best, seg_dist(pt, ring[i - 1], ring[i]))
    return best


def bbox_hit(a, b, pad=0.0):
    return a[0] - pad <= b[2] and b[0] - pad <= a[2] and a[1] - pad <= b[3] and b[1] - pad <= a[3]


def near(pt, bb, pad):
    return bb[0] - pad <= pt[0] <= bb[2] + pad and bb[1] - pad <= pt[1] <= bb[3] + pad


def dist_to_shape(pt, shape):
    """0 inside, else distance to the outline (metres)."""
    return 0.0 if in_shape(pt, shape) else edge_dist(pt, shape)


def shape_bbox(shape):
    xs = [x for o, _ in shape for x, _ in o]
    ys = [y for o, _ in shape for _, y in o]
    return min(xs), min(ys), max(xs), max(ys)


def overlap(shape_a, shape_b, step=1.0):
    """(area of A in m2, share of A inside B) by sampling a 1 m grid."""
    x0, y0, x1, y1 = shape_bbox(shape_a)
    n = inside = 0
    y = y0 + step / 2
    while y < y1:
        x = x0 + step / 2
        while x < x1:
            if in_shape((x, y), shape_a):
                n += 1
                if in_shape((x, y), shape_b):
                    inside += 1
            x += step
        y += step
    return n * step * step, (inside / n if n else 0.0)


def iou(a, b):
    """Intersection over union of two shapes, in percent (None if either is missing)."""
    if not a or not b or not bbox_hit(shape_bbox(a), shape_bbox(b)):
        return None if not a or not b else 0
    area_a, share = overlap(a, b)
    area_b, _ = overlap(b, a)
    inter = share * area_a
    return round(100 * inter / (area_a + area_b - inter)) if area_a + area_b - inter > 0 else None


def norm_name(s):
    s = html.unescape(str(s or '')).lower().replace('&', 'and')
    s = re.sub(r'\b(the|hall|building|bldg|center|centre)\b', ' ', s)
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def r6(v):
    return round(v, 6)


def solve(A, b):
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(M[r][i]))
        M[i], M[piv] = M[piv], M[i]
        for r in range(n):
            if r != i:
                f = M[r][i] / M[i][i]
                for c in range(i, n + 1):
                    M[r][c] -= f * M[i][c]
    return [M[i][n] / M[i][i] for i in range(n)]


def fit_affine(pairs):
    S = [[0.0] * 3 for _ in range(3)]
    bx = [0.0] * 3
    by = [0.0] * 3
    for (u, v), (x, y) in pairs:
        r = [u, v, 1.0]
        for i in range(3):
            for j in range(3):
                S[i][j] += r[i] * r[j]
            bx[i] += r[i] * x
            by[i] += r[i] * y
    return solve(S, bx), solve(S, by)


def apply_affine(T, u, v):
    return (T[0][0] * u + T[0][1] * v + T[0][2], T[1][0] * u + T[1][1] * v + T[1][2])


# ---------------------------------------------------------------------------
# downloads (cached)
# ---------------------------------------------------------------------------
class Fetcher:
    def __init__(self, cache, refresh):
        self.cache, self.refresh = cache, refresh
        os.makedirs(cache, exist_ok=True)

    def get(self, url, name, pause=0.5):
        path = os.path.join(self.cache, name)
        if os.path.exists(path) and not self.refresh:
            return path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://map.ucla.edu/'})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as res:
                    data = res.read()
                break
            except urllib.error.HTTPError:
                raise
            except Exception as e:  # network hiccup
                if attempt == 2:
                    raise
                print(f'  retry {name}: {e}', file=sys.stderr)
                time.sleep(3)
        with open(path + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(path + '.tmp', path)
        time.sleep(pause)
        return path

    def json(self, url, name, pause=0.3):
        with open(self.get(url, name, pause), encoding='utf-8') as f:
            return json.load(f)


def fetch_osm(fx):
    """Grid of bbox tiles; any tile the API refuses (too many nodes) is split in four."""
    paths = []

    def one(b):
        name = 'osm/tile_%s_%s_%s_%s.osm' % tuple(round(v, 4) for v in b)
        try:
            paths.append(fx.get(OSM_API.format(*[round(v, 4) for v in b]), name, pause=1.0))
        except urllib.error.HTTPError as e:
            if e.code not in (400, 509) or b[2] - b[0] < 0.0005:
                raise
            mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            for q in ((b[0], b[1], mx, my), (mx, b[1], b[2], my), (b[0], my, mx, b[3]), (mx, my, b[2], b[3])):
                one(q)

    lon = BBOX[0]
    while lon < BBOX[2] - 1e-9:
        lat = BBOX[1]
        while lat < BBOX[3] - 1e-9:
            one((lon, lat, min(lon + TILE[0], BBOX[2]), min(lat + TILE[1], BBOX[3])))
            lat += TILE[1]
        lon += TILE[0]
    return paths


def load_osm(paths):
    nodes, ways, rels = {}, {}, {}
    for p in paths:
        for el in ET.parse(p).getroot():
            tags = {t.get('k'): t.get('v') for t in el.findall('tag')}
            if el.tag == 'node':
                nodes[el.get('id')] = (float(el.get('lat')), float(el.get('lon')), tags)
            elif el.tag == 'way':
                ways[el.get('id')] = ([n.get('ref') for n in el.findall('nd')], tags)
            elif el.tag == 'relation':
                rels[el.get('id')] = ([(m.get('type'), m.get('ref'), m.get('role')) for m in el.findall('member')], tags)
    return nodes, ways, rels


def stitch(way_lists):
    """Join open ways into closed rings."""
    rings, open_ = [], [list(w) for w in way_lists if w]
    while open_:
        cur = open_.pop(0)
        changed = True
        while cur[0] != cur[-1] and changed:
            changed = False
            for i, w in enumerate(open_):
                if w[0] == cur[-1]:
                    cur += w[1:]
                elif w[-1] == cur[-1]:
                    cur += w[::-1][1:]
                elif w[-1] == cur[0]:
                    cur = w[:-1] + cur
                elif w[0] == cur[0]:
                    cur = w[::-1][:-1] + cur
                else:
                    continue
                open_.pop(i)
                changed = True
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
    return rings


def osm_buildings(nodes, ways, rels):
    """[{id, name, tags, shape:[(outer,[holes])], ring_ids}] in projected metres."""
    out = []
    in_rel = set()
    for rid, (mem, t) in rels.items():
        if t.get('type') != 'multipolygon' or 'building' not in t:
            continue
        outer = [ways[r][0] for typ, r, role in mem if typ == 'way' and role == 'outer' and r in ways]
        inner = [ways[r][0] for typ, r, role in mem if typ == 'way' and role == 'inner' and r in ways]
        for typ, r, role in mem:
            if typ == 'way':
                in_rel.add(r)
        o_r, i_r = stitch(outer), stitch(inner)
        if not o_r or any(n not in nodes for ring in o_r + i_r for n in ring):
            continue
        P = lambda ring: [proj(nodes[n][0], nodes[n][1]) for n in ring[:-1]]
        shape = []
        for o in o_r:
            po = P(o)
            holes = [P(h) for h in i_r if pip(P(h)[0], po)]
            shape.append((po, holes))
        out.append(dict(id='relation/' + rid, name=t.get('name', ''), tags=t, shape=shape,
                        node_ids=set(n for ring in o_r + i_r for n in ring)))
    for wid, (nd, t) in ways.items():
        if 'building' not in t or nd[0] != nd[-1] or len(nd) < 4 or any(n not in nodes for n in nd):
            continue
        if wid in in_rel and 'name' not in t:
            continue
        out.append(dict(id='way/' + wid, name=t.get('name', ''), tags=t,
                        shape=[([proj(nodes[n][0], nodes[n][1]) for n in nd[:-1]], [])], node_ids=set(nd)))
    return out


def esri_shape(geom):
    """GeoJSON Polygon/MultiPolygon (lon,lat) -> [(outer,[holes])] projected."""
    polys = geom['coordinates'] if geom['type'] == 'MultiPolygon' else [geom['coordinates']]
    shape = []
    for poly in polys:
        rings = [[proj(lat, lon) for lon, lat in ring[:-1]] for ring in poly]
        if not rings:
            continue
        # ArcGIS may list several outer rings in one polygon; split by containment
        outers = [r for r in rings if not any(r is not o and pip(r[0], o) for o in rings)]
        for o in outers:
            shape.append((o, [r for r in rings if r is not o and pip(r[0], o)]))
    return shape


def c3d_details(fx, loc_id):
    d = fx.json(f'{C3D}locations/{loc_id}?map=2043&key={C3D_KEY}', f'c3d/loc_{loc_id}.json', pause=0.2)
    rows = re.findall(r'<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>', d.get('description') or '', re.S)
    clean = lambda s: re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', s))).strip()
    return {clean(a): clean(b) for a, b in rows if clean(b)}


# ---------------------------------------------------------------------------
# the accessibility PDF
# ---------------------------------------------------------------------------
PDF_COLORS = {  # CMYK fills used for the legend dots
    '(0.613, 0.969, 0.0, 0.0)': 'elevator',
    '(0.77, 0.066, 1.0, 0.004)': 'entrance',
    '(0.047, 0.84, 1.0, 0.004)': 'powered',
}
PDF_LABELS = [('ROYCE', 'Royce Hall'), ('POWELL', 'Powell Library'), ('BUNCHE', 'Bunche Hall'), ('KAUFMAN', 'Kaufman Hall'),
              ('PERLOFF', 'Perloff Hall'), ('HEDRICK', 'Hedrick Hall'), ('RIEBER', 'Rieber Hall'),
              ('COVEL', 'Covel Commons (Sunset Village)'), ('ROLFE', 'Rolfe Hall'), ('FOWLER', 'Fowler Museum'), ('MOORE', 'Moore Hall')]


def pdf_points(pdf_path, obuild, log):
    """Georeference the 2020 accessibility map and return its dots as projected points.

    1. Rough affine from building labels to OSM building centroids.
    2. Iterate: pair PDF building shapes with the OSM building their centroid falls in
       (similar area only), refit the affine.
    3. Remaining error is corrected locally by inverse distance weighting of the
       residuals of nearby matched buildings.
    """
    try:
        import pdfplumber
    except ImportError:
        log.append('pdfplumber not installed: skipped the accessibility PDF (elevators will be missing)')
        return [], None
    page = pdfplumber.open(pdf_path).pages[0]
    shapes, dots = [], []
    for c in page.curves:
        col = str(c.get('non_stroking_color'))
        pts = [(x, -y) for x, y in c['pts']]
        if col == '(0.0, 0.0, 0.0, 1.0)' and c['fill']:
            a, cc = area_centroid(pts)
            if abs(a) > 15:
                shapes.append((abs(a), cc))
        elif col in PDF_COLORS and c['fill'] and 3.5 < c['width'] < 7 and 3.5 < c['height'] < 7:
            cx, cy = (c['x0'] + c['x1']) / 2, -(c['top'] + c['bottom']) / 2
            # skip the legend box (bottom left)
            if c['x0'] < 160 and c['top'] > 570:
                continue
            if not any(k == PDF_COLORS[col] and math.hypot(cx - x, cy - y) < 1.5 for k, x, y in dots):
                dots.append((PDF_COLORS[col], cx, cy))
    words = {}
    for w in page.extract_words():
        words.setdefault(w['text'], []).append(((w['x0'] + w['x1']) / 2, -(w['top'] + w['bottom']) / 2))
    centroid = {}
    for b in obuild:
        a, c = area_centroid(b['shape'][0][0])
        b['_area'], b['_c'] = abs(a), c
        centroid.setdefault(b['name'], c)
    pairs = [(words[w][0], centroid[n]) for w, n in PDF_LABELS if w in words and n in centroid]
    T = fit_affine(pairs)
    for _ in range(8):
        det = abs(T[0][0] * T[1][1] - T[0][1] * T[1][0])
        pairs = []
        for a, cc in shapes:
            x, y = apply_affine(T, *cc)
            for b in obuild:
                if pip((x, y), b['shape'][0][0]):
                    if 0.75 < a * det / max(b['_area'], 1) < 1.33:
                        pairs.append((cc, b['_c']))
                    break
        T = fit_affine(pairs)
    res = sorted(math.dist(apply_affine(T, *u), xy) for u, xy in pairs)
    resid = [(apply_affine(T, *u), (xy[0] - apply_affine(T, *u)[0], xy[1] - apply_affine(T, *u)[1])) for u, xy in pairs]
    log.append(f'accessibility PDF: {len(dots)} dots, affine fit on {len(pairs)} buildings, '
               f'median residual {res[len(res) // 2]:.1f} m, 90th pct {res[int(len(res) * .9)]:.1f} m (before local correction)')

    def correct(p):
        ws = []
        for q, d in resid:
            dd = math.dist(p, q)
            if dd < 250:
                ws.append((1 / (dd + 5) ** 2, d))
        if not ws:
            return p
        s = sum(w for w, _ in ws)
        return (p[0] + sum(w * d[0] for w, d in ws) / s, p[1] + sum(w * d[1] for w, d in ws) / s)

    out = [(kind, correct(apply_affine(T, x, y))) for kind, x, y in dots]
    return out, math.sqrt(abs(T[0][0] * T[1][1] - T[0][1] * T[1][0]))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cache', default=os.path.join(HERE, '.cache'), help='raw download directory (git-ignored)')
    ap.add_argument('--refresh', action='store_true', help='download everything again')
    ap.add_argument('--out', default=HERE)
    args = ap.parse_args()
    fx = Fetcher(args.cache, args.refresh)
    log = []
    today = datetime.date.today().isoformat()

    print('OpenStreetMap…')
    nodes, ways, rels = load_osm(fetch_osm(fx))
    obuild = osm_buildings(nodes, ways, rels)
    print(f'  {len(nodes)} nodes, {len(ways)} ways, {len(obuild)} building outlines')

    print('UCLA GIS (map.ucla.edu)…')
    q = 'query?where=1%3D1&outFields=*&outSR=4326&f=geojson'
    areas = fx.json(ARCGIS + 'data/location_area/MapServer/0/' + q, 'ucla/location_area.json')['features']
    bldg_pts = fx.json(ARCGIS + 'data/data/MapServer/2/' + q, 'ucla/buildings.json')['features']
    girr = fx.json(ARCGIS + 'data/feature/MapServer/0/' + q, 'ucla/girr.json')['features']
    pdf_path = fx.get(ACCESS_PDF, 'ucla/Access_Oct_2020.pdf')

    print('UCLA Concept3D map (maps.ucla.edu)…')
    c3d_all = fx.json(f'{C3D}locations?map=2043&key={C3D_KEY}', 'c3d/locations.json')
    c3d_cats = {C3D_POWERED: 'powered', C3D_UNPOWERED: 'unpowered', C3D_ELEVATOR: 'elevator', C3D_RESTROOM: 'restroom'}
    c3d = [x for x in c3d_all if x.get('catId') in c3d_cats]

    print('Accessibility PDF…')
    pdf_dots, pdf_scale = pdf_points(pdf_path, obuild, log)

    c3d_shapes = {}
    for x in c3d_all:
        sh = x.get('shape')
        if isinstance(sh, dict) and sh.get('type') == 'polygon' and sh.get('paths'):
            try:
                paths = json.loads(sh['paths']) if isinstance(sh['paths'], str) else sh['paths']
                ring = [proj(a, b) for a, b in paths]
            except (ValueError, TypeError):
                continue
            if len(ring) >= 3 and norm_name(x['name']) not in c3d_shapes:
                c3d_shapes[norm_name(x['name'])] = [(ring, [])]

    areas_by_name = {}
    for f in areas:
        if f.get('geometry'):
            areas_by_name.setdefault(norm_name(f['properties']['NAM']), []).append(f)
    floors_by_name = {norm_name(f['properties']['NAM']): f['properties'].get('FLR_QTY') for f in bldg_pts}

    records = FP.load_records(os.path.join(args.out, 'plans'))
    for code, recs in records.items():
        for rec in recs:
            fit = rec.get('fit') or {}
            log.append(f"floor record {rec['file']}: {code} floor {rec['floor']}, {len(rec['rooms'])} rooms, "
                       f"{len(rec.get('walls', []))} wall lines, fit max {fit.get('max_m')} m on {fit.get('points')} points")
    index = {'generated': today, 'buildings': {}, 'aliases': {}, 'sources': SOURCES}
    coverage = {}
    feature_owner = {}  # c3d id -> code, so a door is used once
    pdf_check = []

    # ---- pass 1: footprints
    for code, cfg in B.items():
        names = [norm_name(n) for n in cfg['ucla']]
        ucla_feats = [f for n in names for f in areas_by_name.get(n, [])]
        ucla_shape = [p for f in ucla_feats for p in esri_shape(f['geometry'])]
        chosen = []
        for ob in obuild:
            if ob['name'] in cfg.get('osm', []):
                chosen.append(ob)
        if ucla_shape:
            # OSM outlines that sit mostly inside UCLA's outline for this building
            ub = shape_bbox(ucla_shape)
            for ob in obuild:
                if ob in chosen or not bbox_hit(shape_bbox(ob['shape']), ub):
                    continue
                if overlap(ob['shape'], ucla_shape, step=1.5)[1] > 0.6:
                    chosen.append(ob)
            # a named OSM outline must also overlap UCLA's (several Hill buildings share a name)
            chosen = [ob for ob in chosen if bbox_hit(shape_bbox(ob['shape']), ub)
                      and overlap(ob['shape'], ucla_shape, 1.5)[1] > 0.3]
        # drop outlines nested inside a bigger chosen outline (building:part and the like)
        sizes = {id(ob): overlap(ob['shape'], ob['shape'], 1.5)[0] for ob in chosen}
        chosen = [ob for ob in chosen if not any(
            o2 is not ob and sizes[id(o2)] > sizes[id(ob)] and bbox_hit(shape_bbox(ob['shape']), shape_bbox(o2['shape']))
            and overlap(ob['shape'], o2['shape'], 1.5)[1] > 0.9 for o2 in chosen)]
        c3d_shape = [p for n in names for p in c3d_shapes.get(n, [])]
        osm_shape = [p for ob in chosen for p in ob['shape']]
        check = {'ucla': iou(osm_shape, ucla_shape), 'c3d': iou(osm_shape, c3d_shape)} if osm_shape else {}
        if osm_shape and ucla_shape and (check['ucla'] or 0) < 60 and c3d_shape and \
                (iou(ucla_shape, c3d_shape) or 0) > (check['c3d'] or 0) + 10:
            # OSM and UCLA disagree and UCLA's own second map sides with the UCLA outline
            log.append(f"{code}: OSM outline matches UCLA GIS {check['ucla']}% and Concept3D {check['c3d']}%; "
                       f"UCLA GIS outline matches Concept3D {iou(ucla_shape, c3d_shape)}%, so the UCLA outline is used")
            chosen = []
        if chosen:
            shape, src, fp_ids = osm_shape, 'osm', [ob['id'] for ob in chosen]
        elif ucla_shape:
            shape, src = ucla_shape, 'ucla_gis'
            fp_ids = ['UCLA location ' + str(f['properties']['ID']) for f in ucla_feats]
            check = {'c3d': iou(ucla_shape, c3d_shape)}
        else:
            log.append(f'{code}: no outline found')
            continue
        cfg['_shape'], cfg['_src'], cfg['_ids'], cfg['_check'] = shape, src, fp_ids, check
        cfg['_bb'] = shape_bbox(shape)
        cfg['_osm_nodes'] = set().union(*[ob['node_ids'] for ob in chosen]) if chosen else set()
        cfg['_floors'] = next((floors_by_name[n] for n in names if floors_by_name.get(n)), None)

    def owner_of(pt, max_d):
        best, bd = None, max_d
        for code, cfg in B.items():
            if '_shape' not in cfg or not near(pt, cfg['_bb'], max_d):
                continue
            d = dist_to_shape(pt, cfg['_shape'])
            if d <= bd:
                best, bd = code, d
        return best

    name_to_code = {}
    for code, cfg in B.items():
        for n in cfg['ucla'] + [cfg['name']]:
            name_to_code[norm_name(n)] = code

    # ---- pass 2: features
    for code, cfg in B.items():
        if '_shape' not in cfg:
            continue
        shape, bb = cfg['_shape'], cfg['_bb']
        feats = []
        pib = dict(wing_prefixes=cfg.get('wing_prefixes', ()), level_first_letter=cfg.get('level_first_letter', False))

        # OSM doors, steps, elevators, toilets
        for nid, (lat, lon, t) in nodes.items():
            if not t:
                continue
            p = proj(lat, lon)
            on_outline = nid in cfg['_osm_nodes']
            if not on_outline and not near(p, bb, 5):
                continue
            if 'entrance' in t or 'door' in t:
                if not on_outline and dist_to_shape(p, shape) > 3:
                    continue
                if owner_of(p, 3) not in (code, None) and not on_outline:
                    continue
                kind = t.get('entrance', 'yes')
                f = dict(t='stairs' if kind == 'staircase' else 'entrance', lat=r6(lat), lon=r6(lon), src='osm', acc='mapped',
                         ref=f'node/{nid}')
                bits = []
                if kind == 'main':
                    bits.append('main entrance')
                elif kind == 'staircase':
                    bits.append('stairwell door')
                elif kind == 'service':
                    bits.append('service door')
                if t.get('wheelchair') == 'yes':
                    bits.append('wheelchair accessible')
                elif t.get('wheelchair') == 'no':
                    bits.append('not wheelchair accessible')
                if t.get('door') in ('automatic',) or t.get('automatic_door') == 'yes':
                    bits.append('automatic door')
                if t.get('name'):
                    f['name'] = t['name']
                if t.get('level'):
                    f['osm_level'] = t['level']
                f['note'] = ', '.join(bits)
                feats.append(f)
            elif t.get('highway') == 'elevator' and dist_to_shape(p, shape) <= 3:
                f = dict(t='elevator', lat=r6(lat), lon=r6(lon), src='osm', acc='mapped', ref=f'node/{nid}', note='')
                if t.get('level'):
                    f['osm_level'] = t['level']
                feats.append(f)
            elif t.get('amenity') == 'toilets' and in_shape(p, shape):
                f = dict(t='restroom', lat=r6(lat), lon=r6(lon), src='osm', acc='mapped', ref=f'node/{nid}', note='')
                if t.get('level'):
                    f['osm_level'] = t['level']
                feats.append(f)
        for wid, (nd, t) in ways.items():
            if t.get('highway') != 'steps' or any(n not in nodes for n in nd):
                continue
            line = [proj(nodes[n][0], nodes[n][1]) for n in nd]
            if not any(near(p, bb, 3) for p in line):
                continue
            if not any(n in cfg['_osm_nodes'] for n in nd) and min(dist_to_shape(p, shape) for p in line) > 2:
                continue
            inside = all(in_shape(p, shape) for p in line)
            feats.append(dict(t='steps', line=[[r6(nodes[n][0]), r6(nodes[n][1])] for n in nd], src='osm', acc='mapped',
                              ref=f'way/{wid}', note='indoor steps' if inside else 'outdoor steps at the building'))

        # Concept3D doors / elevators / accessible restrooms
        for x in c3d:
            kind = c3d_cats[x['catId']]
            p = proj(x['lat'], x['lng'])
            if not near(p, bb, 40) or dist_to_shape(p, shape) > 40:
                continue
            det = c3d_details(fx, x['id'])
            bname = det.get('Building Name') or ''
            if kind == 'restroom':
                bname = re.sub(r'\s+[A-Z]*\d+[A-Z]?$', '', det.get('Building name and room number', x['name'])).strip()
            by_name = name_to_code.get(norm_name(bname))
            if by_name is None:
                # try prefixes ("Boelter 1765" -> "boelter")
                for k, v in name_to_code.items():
                    if norm_name(bname) and (norm_name(bname) == k.split(' ')[0] or k.startswith(norm_name(bname) + ' ')):
                        by_name = v
                        break
            if by_name != code and not (by_name is None and owner_of(p, 6) == code):
                continue
            if feature_owner.get(x['id']):
                continue
            feature_owner[x['id']] = code
            name = html.unescape(x['name']).strip()
            if kind in ('powered', 'unpowered'):
                dt = det.get('Door Type', '')
                bits = []
                if kind == 'powered':
                    bits.append('powered door')
                if det.get('Marked as Accessible', '').startswith('Yes'):
                    bits.append('marked accessible')
                if det.get('Path Types'):
                    bits.append('approach: ' + det['Path Types'].lower())
                if det.get('Access Restrictions') and det['Access Restrictions'] != 'Public':
                    bits.append(det['Access Restrictions'].lower())
                f = dict(t='elevator' if 'Elevator' in dt else 'entrance', lat=r6(x['lat']), lon=r6(x['lng']), src='c3d',
                         acc='surveyed', name=name, note=', '.join(bits), ref=f"concept3d {x['id']}")
                if det.get('Cardinal'):
                    f['side'] = det['Cardinal']
                m = re.search(r'\b(?:floor|level)\s*([0-9A-Z])\b', name, re.I)
                if m:
                    f['floor'] = m.group(1).upper()
                if kind == 'powered':
                    f['powered'] = True
                feats.append(f)
            elif kind == 'elevator':
                feats.append(dict(t='elevator', lat=r6(x['lat']), lon=r6(x['lng']), src='c3d', acc='surveyed', name=name,
                                  note='outdoor or entrance elevator', ref=f"concept3d {x['id']}"))
            elif kind == 'restroom':
                room = re.search(r'([A-Z]*\d+[A-Z]?)\s*$', det.get('Building name and room number', name))
                room = room.group(1) if room else ''
                gender = det.get('Restroom gender', '')
                bits = ['accessible restroom']
                if gender:
                    bits.append(gender.lower())
                f = dict(t='restroom', lat=r6(x['lat']), lon=r6(x['lng']), src='c3d', acc='surveyed', room=room,
                         floor=floor_of(room, **pib), note=', '.join(bits), ref=f"concept3d {x['id']}")
                f['name'] = f'Restroom {room}' if room else 'Restroom'
                feats.append(f)

        # UCLA gender inclusive restrooms (room + floor + position)
        rule_checked = rule_ok = 0
        for g in girr:
            pr = g['properties']
            gb = name_to_code.get(norm_name(pr.get('BLDG')))
            if gb is None:
                for n in cfg['ucla']:
                    if norm_name(pr.get('BLDG')) == norm_name(n):
                        gb = code
            if gb != code:
                continue
            lon, lat = g['geometry']['coordinates']
            room = str(pr.get('ROOM') or '').strip()
            flr = str(pr.get('FLOR') or '').strip() or None
            if room and flr:
                rule_checked += 1
                rule_ok += floor_of(room, **pib) == flr
            feats.append(dict(t='restroom', lat=r6(lat), lon=r6(lon), src='ucla_gis', acc='surveyed', room=room, floor=flr,
                              name=f'Restroom {room}' if room else 'Restroom',
                              note=('all-gender, single stall' if 'Single' in (pr.get('TYPE') or '') else 'all-gender, multi stall'),
                              ref=f"UCLA GIRR {pr.get('OBJECTID')}"))

        # Accessibility PDF: elevators (and accessible doors where nothing better exists)
        doors = [proj(f['lat'], f['lon']) for f in feats if f['t'] == 'entrance' and 'lat' in f]
        for kind, p in pdf_dots:
            if not near(p, bb, 12):
                continue
            d = dist_to_shape(p, shape)
            if kind == 'elevator':
                if d > 4 or owner_of(p, 4) != code:
                    continue
                lat, lon = unproj(*p)
                feats.append(dict(t='elevator', lat=r6(lat), lon=r6(lon), src='ucla_pdf', acc='approx',
                                  note='', ref='accessibility map 2020'))
            else:
                if d > 12 or owner_of(p, 12) != code:
                    continue
                if doors:
                    pdf_check.append(min(math.dist(p, q) for q in doors))
                if any(math.dist(p, q) < 15 for q in doors):
                    continue
                lat, lon = unproj(*p)
                feats.append(dict(t='entrance', lat=r6(lat), lon=r6(lon), src='ucla_pdf', acc='approx',
                                  note='powered accessible entrance' if kind == 'powered' else 'accessible entrance',
                                  ref='accessibility map 2020', powered=kind == 'powered'))

        # A door should sit on the outline. One well inside it is either on another level
        # (a bridge or a podium) or placed loosely; say so instead of hiding it.
        for f in feats:
            if f['t'] == 'entrance' and 'lat' in f and f['src'] != 'osm':
                p = proj(f['lat'], f['lon'])
                if in_shape(p, shape):
                    d_in = edge_dist(p, shape)
                    if d_in > 4:
                        f['note'] = (f['note'] + '; ' if f['note'] else '') + f'this point is {d_in:.0f} m inside the mapped outline'
                        if f['acc'] == 'surveyed':
                            f['acc'] = 'approx'

        # Restroom points that are really a building placeholder: same spot, same floor, different rooms.
        spots = {}
        for f in feats:
            if f['t'] == 'restroom' and f.get('room'):
                spots.setdefault((f['lat'], f['lon'], f.get('floor')), set()).add(f['room'])
        for f in feats:
            if f['t'] == 'restroom' and len(spots.get((f['lat'], f['lon'], f.get('floor')), ())) >= 3:
                f['acc'] = 'building'
                f['note'] += '; UCLA lists this room but not where it is in the building'
        # de-duplicate restrooms listed by both UCLA sources
        seen, uniq = set(), []
        for f in feats:
            if f['t'] == 'restroom' and f.get('room'):
                key = (f['room'], f.get('floor'))
                if key in seen:
                    continue
                seen.add(key)
            uniq.append(f)
        feats = uniq

        # floors evidenced by the data
        levels = sorted({f['floor'] for f in feats if f.get('floor')}, key=level_key)

        outer_ll = [[[r6(v) for v in unproj(x, y)] for x, y in o] for o, _ in shape]
        holes_ll = [[[[r6(v) for v in unproj(x, y)] for x, y in h] for h in holes] for _, holes in shape]
        cx, cy = shape_centroid(shape)
        clat, clon = unproj(cx, cy)
        doc = {
            'code': code,
            'name': cfg['name'],
            'center': [r6(clat), r6(clon)],
            'floors_ucla': cfg.get('_floors'),
            'outline': {'polys': [{'outer': o, 'holes': h} for o, h in zip(outer_ll, holes_ll)], 'src': cfg['_src'],
                        'ref': cfg['_ids'], 'agree_pct': cfg['_check']},
            'floor_rule': {'wing_prefixes': cfg.get('wing_prefixes', []), 'level_first_letter': cfg.get('level_first_letter', False),
                           'checked': rule_checked, 'agree': rule_ok},
            'levels': levels,
            'features': sorted(feats, key=lambda f: (f['t'], f.get('floor') or '', f.get('name') or '')),
        }
        # floors traced from published plans (maps/plans/*.json, written by trace_plan.py)
        FP.merge_into(doc, records.get(code, []))
        rooms = doc.get('rooms', {})
        with open(os.path.join(args.out, safe_name(code) + '.json'), 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))
        index['buildings'][code] = {
            'name': cfg['name'], 'file': safe_name(code) + '.json', 'center': doc['center'], 'hill': bool(cfg.get('hill')),
            'outline': [simplify(o, 0.8) for o in outer_ll],
        }
        for a in cfg.get('aliases', []):
            index['aliases'][a] = code
        cnt = lambda t, srcs=None: sum(1 for f in feats if f['t'] == t and (srcs is None or f['src'] in srcs))
        coverage[code] = dict(
            outline=cfg['_src'], outline_ref=', '.join(cfg['_ids'][:3]) + (' …' if len(cfg['_ids']) > 3 else ''),
            agree=cfg['_check'],
            entrances=cnt('entrance'), ent_c3d=cnt('entrance', {'c3d'}), ent_osm=cnt('entrance', {'osm'}), ent_pdf=cnt('entrance', {'ucla_pdf'}),
            elevators=cnt('elevator'), elev_pdf=cnt('elevator', {'ucla_pdf'}), elev_osm=cnt('elevator', {'osm'}), elev_c3d=cnt('elevator', {'c3d'}),
            stair_doors=cnt('stairs'), steps=cnt('steps'),
            wc_gis=sum(1 for f in feats if f['t'] == 'restroom' and f['src'] == 'ucla_gis' and f['acc'] != 'building'),
            wc_c3d=sum(1 for f in feats if f['t'] == 'restroom' and f['src'] == 'c3d' and f['acc'] != 'building'),
            wc_osm=cnt('restroom', {'osm'}),
            stairs=cnt('stairs') + cnt('steps'),
            restrooms=sum(1 for f in feats if f['t'] == 'restroom' and f['acc'] != 'building'),
            restrooms_listed=cnt('restroom'),
            rule=f'{rule_ok}/{rule_checked}' if rule_checked else 'no UCLA floor records',
            plans=FP.coverage_text(records.get(code, [])),
            rooms_placed=sorted(rooms),
            floors=cfg.get('_floors'))

    if pdf_check:
        pdf_check.sort()
        log.append(f'PDF accessible-entrance dots vs nearest surveyed/OSM door: median {pdf_check[len(pdf_check) // 2]:.1f} m, '
                   f'90th pct {pdf_check[int(len(pdf_check) * .9)]:.1f} m (n={len(pdf_check)})')

    with open(os.path.join(args.out, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump(index, fh, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(args.out, 'coverage.json'), 'w', encoding='utf-8') as fh:
        json.dump({'generated': today, 'notes': log, 'buildings': coverage}, fh, ensure_ascii=False, indent=1)
    write_coverage_md(os.path.join(args.out, 'SOURCES.md'), coverage, today)
    FP.write_plans_md(os.path.join(args.out, 'SOURCES.md'), records, today)
    for line in log:
        print('note:', line)
    print(f'wrote {len(index["buildings"])} buildings to {args.out}')


def write_coverage_md(path, coverage, today):
    """Rewrite the coverage table between the markers in maps/SOURCES.md."""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    a, b = '<!-- coverage:start -->', '<!-- coverage:end -->'
    if a not in text or b not in text:
        return

    def parts(total, pairs, none='missing'):
        if not total:
            return none
        inner = ', '.join(f'{label} {n}' for label, n in pairs if n)
        return f'{total} ({inner})' if inner else str(total)

    rows = ['| Code | Outline | Doors | Elevators | Stairs | Restrooms | Rooms and corridors | Floor from room number |',
            '|---|---|---|---|---|---|---|---|']
    for code, c in coverage.items():
        ag = c['agree'] or {}
        agree = ', '.join(f"{k.replace('ucla', 'UCLA GIS').replace('c3d', 'UCLA map')} {v}%" for k, v in ag.items() if v is not None)
        outline = ('OSM' if c['outline'] == 'osm' else 'UCLA GIS') + (f' ({agree})' if agree else '')
        doors = parts(c['entrances'], [('survey', c['ent_c3d']), ('OSM', c['ent_osm']), ('2020 map, approx.', c['ent_pdf'])])
        elev = parts(c['elevators'], [('2020 map, approx.', c['elev_pdf']), ('OSM', c['elev_osm']), ('survey', c['elev_c3d'])])
        stairs = parts(c['stair_doors'] + c['steps'], [('stair doors', c['stair_doors']), ('outdoor steps', c['steps'])],
                       none='missing (no public source has indoor stairwells)')
        listed = c['restrooms_listed'] - c['restrooms']
        wc = parts(c['restrooms'], [('UCLA GIS', c['wc_gis']), ('survey', c['wc_c3d']), ('OSM', c['wc_osm'])])
        if listed:
            wc += f'; {listed} more listed without a position'
        rooms = '; '.join(c['plans']) if c['plans'] else 'missing (no public plan found)'
        if c['rooms_placed']:
            rooms += f"; {len(c['rooms_placed'])} numbered rooms placed"
        rows.append(f"| {code} | {outline} | {doors} | {elev} | {stairs} | {wc} | {rooms} | {c['rule']} |")
    body = f'{a}\n_Generated by build_maps.py on {today}._\n\n' + '\n'.join(rows) + f'\n{b}'
    text = text[:text.index(a)] + body + text[text.index(b) + len(b):]
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def level_key(lv):
    order = {'C': -3, 'B': -2, 'A': -1, 'CS': -0.5, '0': 0, 'M': 1.5, 'P': 0.5, 'G': 0.25, 'L': 0.1}
    if lv in order:
        return (order[lv], lv)
    return (int(lv), lv) if lv.isdigit() else (50, lv)


def shape_centroid(shape):
    tot = sx = sy = 0.0
    for o, _ in shape:
        a, (cx, cy) = area_centroid(o)
        tot += abs(a)
        sx += abs(a) * cx
        sy += abs(a) * cy
    return (sx / tot, sy / tot) if tot else shape[0][0][0]


def simplify(ring, tol_m):
    """Douglas-Peucker on a lat/lon ring (for the campus overview only)."""
    pts = [proj(a, b) for a, b in ring]
    if len(pts) < 5:
        return ring
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        best, k = 0, None
        for m in range(i + 1, j):
            d = seg_dist(pts[m], pts[i], pts[j])
            if d > best:
                best, k = d, m
        if k is not None and best > tol_m:
            keep[k] = True
            stack += [(i, k), (k, j)]
    return [ring[i] for i in range(len(ring)) if keep[i]]


def safe_name(code):
    return re.sub(r'[^A-Za-z0-9]+', '_', code).strip('_')


SOURCES = {
    'osm': {'label': 'OpenStreetMap contributors (ODbL)', 'url': 'https://www.openstreetmap.org/copyright'},
    'ucla_gis': {'label': 'UCLA campus map GIS (map.ucla.edu)', 'url': 'https://map.ucla.edu/'},
    'c3d': {'label': 'UCLA interactive campus map, accessibility survey (maps.ucla.edu)', 'url': 'https://www.maps.ucla.edu/?id=2043'},
    'ucla_pdf': {'label': 'UCLA Campus Accessibility Map, Oct 2020 (PDF), georeferenced by this script',
                 'url': ACCESS_PDF},
}

if __name__ == '__main__':
    main()
