#!/usr/bin/env python3
"""Trace a published floor plan (image or PDF page) into georeferenced rooms and walls.

    python maps/trace_plan.py maps/plans/YRL-1.control.json          # trace, write the floor record, merge into maps/YRL.json
    python maps/trace_plan.py CONTROL --debug DIR                     # also write overlay PNGs to check the trace
    python maps/trace_plan.py CONTROL --dry-run --debug DIR           # trace and report only
    python maps/trace_plan.py --render PLAN.pdf --page 2 --dpi 150 --out grid.png
                                                                      # rasterize with a pixel grid, to pick control points
    python maps/trace_plan.py --merge-all                             # re-merge every floor record into maps/<CODE>.json

Needs: numpy, opencv-python-headless, Pillow, scikit-image, scipy; pypdfium2 and
pdfplumber for PDFs; pytesseract + the tesseract binary for OCR of raster plans
(vector PDFs give their text directly, so OCR isn't needed there).

How it works
------------
1. The plan is loaded as a raster: an image as is, a PDF page rendered at
   `plan.dpi` (default 150). All pixel coordinates in the control file are in
   that raster.
2. Georeference: 2 to 4 (or more) control points pair plan pixels with ground
   positions. A point's ground position can be given as `ll` [lat, lon], as a
   `feature` already in maps/<CODE>.json ({"t": "restroom", "room": "11940"}),
   or as an `outline_vertex` [polygon, vertex] of the building outline. A
   similarity transform (scale, rotation, shift) is fitted, or an affine one
   with `"transform": "affine"` and 3+ points. Residuals and leave-one-out
   errors are reported and stored.
3. Walls. Three modes:
   - "walls": dark pixels are walls (raster plans, scans, evacuation maps).
     Text found by OCR is erased first, then specks and glyphs (components
     shorter than `min_wall_m`). Thin lines (door swings, furniture) can be
     removed with `open_px`.
   - "vector": a vector PDF. Line, rect and curve objects are drawn straight
     from the PDF (pdfplumber), filtered by `min_lw_pt`, so text never gets
     into the walls, and the room labels come from the PDF text with exact
     positions.
   - "fills": diagram maps where each room is a solid colored block (like the
     library's Research Commons map). Rooms are the colored regions.
4. Door gaps are sealed: from every wall end the wall is extended along its
   own direction for up to `door_m`; if it meets another wall the gap is
   closed. Hand fixes: `cuts` (lines added as walls) and `erase` (lines
   removed).
5. Rooms are the enclosed regions (connected components of the free space).
   Regions touching the edge of the plan, outside the building outline, too
   small (`min_room_m2`) or too thin (wall cavities) are dropped.
6. Labels (OCR or PDF text, plus manual `labels`) are snapped to the region
   they sit in. A token like 2444, G05, B120A is the room number (`ref`). Words
   pick the kind: stair, elevator, restroom, mech, corridor, exit. Unlabeled
   long thin regions are corridors; other unlabeled regions are "other".
   A region with two room numbers is flagged "merged" and gets no ref (fix with
   a cut). `set` overrides kind/label/ref of the region under a pixel, `drop`
   removes the region under a pixel, `add` adds hand-placed elements (polygons
   in pixels, or points like tables) that the plan shows but the tracer can't
   pick up.
7. Polygons are grown by half a wall (so neighbours meet at the wall's centre
   line), simplified with Douglas-Peucker (`simplify_m`, default 0.3 m) and
   transformed to lat/lon. Walls are the skeleton of the wall mask, simplified
   the same way.
8. Output: maps/plans/<CODE>-<floor>.json (the floor record, see floorplans.py),
   merged into maps/<CODE>.json, and the plan table in maps/SOURCES.md.

Control file (maps/plans/<CODE>-<floor>.control.json)
-----------------------------------------------------
{
  "building": "YRL", "floor": "1",
  "title": "Research Commons", "extent": "partial",          # or "full"
  "source": {"publisher": "UCLA Library", "what": "...", "url": "...", "page": "...", "date": "2024", "accessed": "..."},
  "plan": {"file": "../../../plans-found/YRL/x.png", "page": 1, "dpi": 150},   # relative to the control file
  "mode": "walls" | "vector" | "fills",
  "transform": "similarity",
  "control": [{"px": [294.5, 297.5], "feature": {"t": "restroom", "room": "11940"}, "what": "restroom 11940"}, ...],
  "crop": [x0, y0, x1, y1],                                   # optional: only trace inside this box
  "params": {"door_m": 2.0, "min_room_m2": 1.0, "simplify_m": 0.3, "dark": 110, "open_px": 0, ...},
  "ocr": true, "labels": [{"px": [x, y], "text": "2444"}],
  "set": [{"px": [x, y], "kind": "stair"}], "drop": [[x, y]],
  "cuts": [[[x0, y0], [x1, y1]]], "erase": [[[x0, y0], [x1, y1]]],
  "add": [{"kind": "pod", "pt": [x, y], "ref": "R01", "label": "R01"}],
  "how": "...", "notes": ["..."]
}
"""
import argparse
import datetime
import json
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import floorplans as FP  # noqa: E402
from build_maps import proj, unproj, r6, dist_to_shape  # noqa: E402

DEFAULTS = {
    'door_m': 2.0,          # longest gap closed by extending a wall end
    'min_room_m2': 1.0,     # smaller regions are dropped
    'min_halfwidth_m': 0.3, # regions thinner than this are wall cavities
    'simplify_m': 0.3,      # Douglas-Peucker tolerance
    'min_wall_m': 0.8,      # dark components shorter than this are text/specks
    'min_wall_line_m': 0.6, # wall centre lines shorter than this are dropped
    'dark': None,           # gray level below which a pixel is ink (None: Otsu)
    'white': 225,           # fills mode: brighter than this on every channel is paper
    'open_px': 0,           # walls mode: opening kernel, removes lines thinner than this
    'exclude_colors': [],   # fills mode: [[r, g, b, tol], ...] colors that aren't rooms
    'outside_m': 3.0,       # regions whose label point is this far outside the outline are dropped
    'min_lw_pt': 0.0,       # vector mode: thinner strokes are ignored
    'ocr_scale': 2,         # OCR on an upscaled copy
    'ocr_conf': 55,
    'corridor_halfwidth_m': 1.6,
    'grow_px': None,        # how far rooms are grown into the walls (None: half the wall)
}

ROOMNUM = re.compile(r'^[A-Z]{0,2}\d{1,5}[A-Z]{0,2}$')
KIND_WORDS = [
    ('stair', re.compile(r'\bSTAIR|\bSTAIRS\b|\bSTR\b|\bSTAIRWELL', re.I)),
    ('elevator', re.compile(r'\bELEV|\bLIFT\b', re.I)),
    ('restroom', re.compile(r"\bMEN'?S?\b|\bWOMEN|\bRESTROOM|\bTOILET|\bW\.?C\.?$|^WC\b|\bRR\b|\bLAV\b|\bLAVATORY|\bGENDER|\bBATH", re.I)),
    ('exit', re.compile(r'\bEXIT\b', re.I)),
    ('mech', re.compile(r'\bMECH|\bELEC\b|\bELECT|\bJAN\b|\bJANITOR|\bCUST\b|\bCUSTODIAL|\bTEL\b|\bIDF\b|\bMDF\b|\bSHAFT|\bUTIL', re.I)),
    ('corridor', re.compile(r'\bCORR\b|\bCORRIDOR|\bHALLWAY|\bLOBBY|\bVESTIBULE|\bVEST\b|\bFOYER|\bPASSAGE', re.I)),
]


def say(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load_raster(path, page=1, dpi=150):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    if path.lower().endswith('.pdf'):
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(path)
        im = pdf[page - 1].render(scale=dpi / 72).to_pil()
    else:
        im = Image.open(path)
    if im.mode in ('RGBA', 'LA', 'P', 'PA'):
        im = im.convert('RGBA')
        bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    return np.array(im.convert('RGB'))


def plan_path(plan, ctl_path, code):
    """The plan file: absolute, next to the control file, or in maps/.cache/plans/<CODE>/ (downloaded from plan.url)."""
    f = plan['file']
    for cand in ([f] if os.path.isabs(f) else
                 [os.path.join(os.path.dirname(os.path.abspath(ctl_path)), f), os.path.join(HERE, '.cache', 'plans', FP.slug(code), f)]):
        if os.path.exists(cand):
            return os.path.normpath(cand)
    if not plan.get('url'):
        raise SystemExit(f'plan file {f} not found (and no plan.url to download it from)')
    import urllib.request
    dst = os.path.join(HERE, '.cache', 'plans', FP.slug(code), os.path.basename(f))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    say(f'downloading {plan["url"]}')
    req = urllib.request.Request(plan['url'], headers={'User-Agent': 'UclaStudySpace-maps/1.0 (+https://github.com/Taylor-Nilsen/UclaStudySpace)'})
    with urllib.request.urlopen(req, timeout=60) as r, open(dst, 'wb') as fh:
        fh.write(r.read())
    return dst


def pdf_page(path, page):
    import pdfplumber
    pdf = pdfplumber.open(path)
    return pdf, pdf.pages[page - 1]


def _lum(c):
    if c is None:
        return 0.0
    if isinstance(c, (int, float)):
        return float(c)
    c = [float(v) for v in c if isinstance(v, (int, float))]
    if len(c) == 1:
        return c[0]
    if len(c) == 3:
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    if len(c) == 4:  # CMYK
        k = c[3]
        return 1 - min(1, 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] + k)
    return 0.0


def pdf_walls(path, page, dpi, shape, min_lw_pt):
    """Rasterize the PDF's line art (not its text) into a wall mask."""
    import cv2
    pdf, pg = pdf_page(path, page)
    s = dpi / 72.0
    x0, top0 = pg.bbox[0], pg.bbox[1]
    mask = np.zeros(shape, np.uint8)
    n = 0
    for o in list(pg.lines) + list(pg.rects) + list(pg.curves):
        lw = float(o.get('linewidth') or 0)
        if o.get('object_type') == 'rect' and not o.get('pts'):
            pts = [(o['x0'], o['top']), (o['x1'], o['top']), (o['x1'], o['bottom']), (o['x0'], o['bottom']), (o['x0'], o['top'])]
        else:
            pts = o.get('pts') or []
        if len(pts) < 2:
            continue
        P = np.array([[(x - x0) * s, (y - top0) * s] for x, y in pts], np.float64)
        P = np.round(P).astype(np.int32)
        dark_fill = o.get('fill') and _lum(o.get('non_stroking_color')) < 0.5
        if dark_fill and len(P) >= 3:
            cv2.fillPoly(mask, [P], 1)
            n += 1
        if o.get('stroke', True) and lw >= min_lw_pt and _lum(o.get('stroking_color')) < 0.6:
            cv2.polylines(mask, [P], False, 1, max(1, int(round(lw * s))))
            n += 1
    words = []
    for w in pg.extract_words(keep_blank_chars=False, use_text_flow=False):
        words.append({'text': w['text'], 'conf': 100,
                      'box': [(w['x0'] - x0) * s, (w['top'] - top0) * s, (w['x1'] - x0) * s, (w['bottom'] - top0) * s]})
    rot = pg.rotation if hasattr(pg, 'rotation') else 0
    pdf.close()
    if rot:
        say(f'warning: PDF page has /Rotate {rot}; check the overlay, vector coordinates may not match the render')
    return mask.astype(bool), words, n


def ocr_words(img, scale=2, conf=55):
    """Words from tesseract, run on the gray image and its inverse (for light text on dark fills)."""
    try:
        import pytesseract
        import cv2
    except ImportError:
        say('OCR unavailable (pip install pytesseract, apt install tesseract-ocr); use "labels" in the control file')
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    if scale != 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    out = []
    for g in (gray, 255 - gray):
        try:
            d = pytesseract.image_to_data(g, config='--psm 11', output_type=pytesseract.Output.DICT)
        except Exception as e:  # tesseract binary missing
            say('OCR failed:', e)
            return []
        for i, t in enumerate(d['text']):
            t = (t or '').strip().strip('.,;:|!_-—\'"')
            if not t or float(d['conf'][i]) < conf or not re.search(r'[A-Za-z0-9]', t):
                continue
            if len(t) == 1 and not t.isdigit():
                continue
            b = [d['left'][i] / scale, d['top'][i] / scale, (d['left'][i] + d['width'][i]) / scale, (d['top'][i] + d['height'][i]) / scale]
            if any(_iou(b, o['box']) > 0.5 for o in out):
                continue
            out.append({'text': t, 'conf': float(d['conf'][i]), 'box': b})
    return out


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0


# ---------------------------------------------------------------------------
# georeferencing
# ---------------------------------------------------------------------------
def resolve_control(ctl, bdoc):
    pts = []
    for c in ctl:
        if 'll' in c:
            ll = c['ll']
        elif 'feature' in c:
            want = c['feature']
            hits = [f for f in bdoc['features'] if all(str(f.get(k)) == str(v) for k, v in want.items())]
            if len(hits) != 1:
                raise SystemExit(f'control point {c.get("what")}: {len(hits)} features match {want}')
            ll = [hits[0]['lat'], hits[0]['lon']]
        elif 'outline_vertex' in c:
            pi, vi = c['outline_vertex']
            ll = bdoc['outline']['polys'][pi]['outer'][vi]
        else:
            raise SystemExit(f'control point {c} needs ll, feature or outline_vertex')
        pts.append({'px': [float(v) for v in c['px']], 'll': [float(ll[0]), float(ll[1])],
                    'what': c.get('what') or json.dumps(c.get('feature') or c.get('outline_vertex'))})
    return pts


def fit_transform(pts, kind='similarity'):
    """pixel (u right, v down) -> projected metres (x east, y north). Returns a 3x3 matrix."""
    U = np.array([p['px'] for p in pts])
    X = np.array([proj(*p['ll']) for p in pts])
    if kind == 'affine':
        if len(pts) < 3:
            raise SystemExit('affine needs 3+ control points')
        A = np.c_[U, np.ones(len(U))]
        cx, *_ = np.linalg.lstsq(A, X[:, 0], rcond=None)
        cy, *_ = np.linalg.lstsq(A, X[:, 1], rcond=None)
        M = np.array([cx, cy, [0, 0, 1]])
    else:
        if len(pts) < 2:
            raise SystemExit('need 2+ control points')
        # x = a u + b v + tx ; y = b u - a v + ty  (v points down, y points north; no mirror)
        rows, rhs = [], []
        for (u, v), (x, y) in zip(U, X):
            rows += [[u, v, 1, 0], [-v, u, 0, 1]]
            rhs += [x, y]
        a, b, tx, ty = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
        M = np.array([[a, b, tx], [b, -a, ty], [0, 0, 1]])
    return M


def apply(M, u, v):
    return M[0, 0] * u + M[0, 1] * v + M[0, 2], M[1, 0] * u + M[1, 1] * v + M[1, 2]


def georef(pts, kind):
    M = fit_transform(pts, kind)
    res = [math.dist(apply(M, *p['px']), proj(*p['ll'])) for p in pts]
    loo = []
    need = 3 if kind == 'affine' else 2
    if len(pts) > need:
        for i in range(len(pts)):
            Mi = fit_transform(pts[:i] + pts[i + 1:], kind)
            loo.append(math.dist(apply(Mi, *pts[i]['px']), proj(*pts[i]['ll'])))
    mpp = math.sqrt(abs(np.linalg.det(M[:2, :2])))
    rot = math.degrees(math.atan2(M[1, 0], M[0, 0]))  # plan x axis vs east, counterclockwise
    return M, res, loo, mpp, rot


# ---------------------------------------------------------------------------
# image ops
# ---------------------------------------------------------------------------
def otsu(gray):
    import cv2
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return t


def remove_small(mask, min_len_px):
    import cv2
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = np.maximum(st[1:, cv2.CC_STAT_WIDTH], st[1:, cv2.CC_STAT_HEIGHT]) >= min_len_px
    return keep[lab]


def skeleton(mask):
    from skimage.morphology import skeletonize
    return skeletonize(mask)


NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def skel_graph(sk):
    """Polylines (lists of (x, y)) from a 1-px skeleton."""
    H, W = sk.shape
    ys, xs = np.nonzero(sk)
    on = set(zip(ys.tolist(), xs.tolist()))

    def nbrs(p):
        y, x = p
        return [(y + dy, x + dx) for dy, dx in NB if (y + dy, x + dx) in on]
    deg = {p: len(nbrs(p)) for p in on}
    nodes = {p for p, d in deg.items() if d != 2}
    seen = set()
    lines = []
    for n0 in nodes:
        for nb in nbrs(n0):
            e = (n0, nb) if n0 < nb else (nb, n0)
            if e in seen:
                continue
            path = [n0, nb]
            seen.add(e)
            prev, cur = n0, nb
            while cur not in nodes:
                nxt = [q for q in nbrs(cur) if q != prev and ((cur, q) if cur < q else (q, cur)) not in seen]
                if not nxt:
                    break
                q = nxt[0]
                seen.add((cur, q) if cur < q else (q, cur))
                prev, cur = cur, q
                path.append(cur)
            lines.append([(x, y) for y, x in path])
    # pure cycles
    rest = on - {p for ln in lines for p in [(y, x) for x, y in ln]}
    while rest:
        start = rest.pop()
        path, prev, cur = [start], None, start
        while True:
            nxt = [q for q in nbrs(cur) if q != prev and q in rest]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            rest.discard(cur)
            path.append(cur)
        path.append(start)
        if len(path) > 3:
            lines.append([(x, y) for y, x in path])
    return lines


def wall_ends(sk, lines, back=8):
    """(end point, unit direction pointing out of the wall) for every free end of the skeleton."""
    out = []
    for ln in lines:
        if len(ln) < 4 or ln[0] == ln[-1]:
            continue
        for a, b in ((ln, 0), (ln[::-1], 0)):
            end = np.array(a[0], float)
            k = min(back, len(a) - 1)
            d = end - np.array(a[k], float)
            nrm = np.linalg.norm(d)
            if nrm == 0:
                continue
            x, y = a[0]
            cnt = sum(1 for dy, dx in NB if 0 <= y + dy < sk.shape[0] and 0 <= x + dx < sk.shape[1] and sk[y + dy, x + dx])
            if cnt == 1:
                out.append((end, d / nrm))
    return out


def seal_doors(wall, sk_lines, sk, max_px, thick):
    """Extend every wall end along its direction; if it meets a wall within max_px, draw the closing line."""
    import cv2
    H, W = wall.shape
    added = []
    for end, d in wall_ends(sk, sk_lines):
        # start a little past the wall's own pixels
        hit = None
        t = thick + 2
        while t <= max_px:
            x, y = end + d * t
            xi, yi = int(round(x)), int(round(y))
            if not (0 <= xi < W and 0 <= yi < H):
                break
            if wall[yi, xi]:
                hit = (xi, yi)
                break
            t += 1
        if hit:
            added.append(((int(end[0]), int(end[1])), hit))
    out = wall.astype(np.uint8).copy()
    for a, b in added:
        cv2.line(out, a, b, 1, max(2, thick))
    return out.astype(bool), added


def wall_thickness_px(wall, sk):
    import cv2
    if not sk.any():
        return 2
    dt = cv2.distanceTransform(wall.astype(np.uint8), cv2.DIST_L2, 3)
    v = dt[sk]
    return max(1, int(round(2 * float(np.median(v)))))


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------
def _inside(a, b):
    return all(x.start >= y.start and x.stop <= y.stop for x, y in zip(a, b))


def _sub(mask, sl, inner):
    """mask (cut to slice sl) cut again to slice inner, which lies inside sl."""
    return mask[inner[0].start - sl[0].start:inner[0].stop - sl[0].start, inner[1].start - sl[1].start:inner[1].stop - sl[1].start]


def region_at(lab, px, regions, r=6):
    """The region under a pixel, or the only region within r px of it."""
    x, y = int(round(px[0])), int(round(px[1]))
    i = int(lab[y, x])
    if i in regions:
        return i
    win = lab[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
    vals, cnt = np.unique(win[np.isin(win, list(regions))], return_counts=True)
    return int(vals[cnt.argmax()]) if len(vals) else None


def text_lines(ws):
    """Words in reading order: grouped into lines by their vertical centre, then left to right."""
    lines = []
    for w in sorted(ws, key=lambda w: (w['box'][1] + w['box'][3]) / 2):
        cy, h = (w['box'][1] + w['box'][3]) / 2, w['box'][3] - w['box'][1]
        for ln in lines:
            if abs(ln['cy'] - cy) < 0.5 * max(h, ln['h']):
                ln['ws'].append(w)
                break
        else:
            lines.append({'cy': cy, 'h': h, 'ws': [w]})
    return [w for ln in lines for w in sorted(ln['ws'], key=lambda w: w['box'][0])]


def classify_text(text):
    for kind, rx in KIND_WORDS:
        if rx.search(text):
            return kind
    return None


def trace(ctl, ctl_path, debug=None):
    import cv2
    from scipy import ndimage as ndi
    code, floor = ctl['building'], str(ctl['floor'])
    P = dict(DEFAULTS, **ctl.get('params', {}))
    bfile = os.path.join(HERE, FP.slug(code) + '.json')
    with open(bfile, encoding='utf-8') as fh:
        bdoc = json.load(fh)
    plan = ctl['plan']
    path = plan_path(plan, ctl_path, code)
    page, dpi = int(plan.get('page', 1)), float(plan.get('dpi', 150))
    mode = ctl.get('mode', 'walls')
    img = load_raster(path, page, dpi)
    H, W = img.shape[:2]
    say(f'{code} floor {floor}: {os.path.basename(path)} page {page}, {W}x{H} px, mode {mode}')

    # georeference
    cps = resolve_control(ctl['control'], bdoc)
    M, res, loo, mpp, rot = georef(cps, ctl.get('transform', 'similarity'))
    Minv = np.linalg.inv(M)
    to_ll = lambda u, v: [r6(t) for t in unproj(*apply(M, u, v))]
    say(f'  fit: {len(cps)} points, residual max {max(res):.2f} m, {mpp * 100:.1f} cm/px, rotation {rot:.1f} deg'
        + (f', leave-one-out max {max(loo):.2f} m' if loo else ''))
    shape = [([proj(*q) for q in p['outer']], [[proj(*q) for q in h] for h in p['holes']]) for p in bdoc['outline']['polys']]
    outline_px = [np.array([apply(Minv, *proj(*q)) for q in p['outer']], np.float64) for p in bdoc['outline']['polys']]

    roi = np.ones((H, W), bool)
    if ctl.get('crop'):
        x0, y0, x1, y1 = [int(v) for v in ctl['crop']]
        roi[:] = False
        roi[max(0, y0):y1, max(0, x0):x1] = True

    # ink and text
    words = []
    if mode == 'vector':
        wall, words, nvec = pdf_walls(path, page, dpi, (H, W), P['min_lw_pt'])
        say(f'  vector: {nvec} PDF line objects drawn, {len(words)} words')
    else:
        if ctl.get('ocr', True):
            words = ocr_words(img, P['ocr_scale'], P['ocr_conf'])
            say(f'  OCR: {len(words)} words')
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        thr = P['dark'] if P['dark'] is not None else (70 if mode == 'fills' else otsu(gray))
        wall = gray < thr
    for lb in ctl.get('labels', []):
        x, y = lb['px']
        words.append({'text': lb['text'], 'conf': 100, 'box': [x - 1, y - 1, x + 1, y + 1], 'manual': True})
    wall &= roi
    if mode == 'walls':
        for w in words:
            if w.get('manual'):
                continue
            t = w['text']
            if len(t) >= 2 and (ROOMNUM.match(t.upper()) or re.search(r'[A-Za-z]{2}', t)):
                b = [int(math.floor(w['box'][0])) - 1, int(math.floor(w['box'][1])) - 1, int(math.ceil(w['box'][2])) + 1, int(math.ceil(w['box'][3])) + 1]
                wall[max(0, b[1]):b[3], max(0, b[0]):b[2]] = False
        if P['open_px']:
            k = cv2.getStructuringElement(cv2.MORPH_RECT, (int(P['open_px']), int(P['open_px'])))
            wall = cv2.morphologyEx(wall.astype(np.uint8), cv2.MORPH_OPEN, k).astype(bool)
    if mode != 'fills':
        wall = remove_small(wall, P['min_wall_m'] / mpp)
    # hand fixes
    for a, b in ctl.get('erase', []):
        m = wall.astype(np.uint8)
        cv2.line(m, tuple(int(v) for v in a), tuple(int(v) for v in b), 0, max(3, int(P.get('erase_px', 7))))
        wall = m.astype(bool)

    sk = skeleton(wall)
    wpx = wall_thickness_px(wall, sk)
    if mode == 'fills':
        sk = np.zeros_like(wall)
    sk_lines = skel_graph(sk) if mode != 'fills' else []

    # rooms
    sealed, added = (seal_doors(wall, sk_lines, sk, P['door_m'] / mpp, wpx) if mode != 'fills' else (wall.copy(), []))
    sm = sealed.astype(np.uint8)
    for a, b in ctl.get('cuts', []):
        cv2.line(sm, tuple(int(v) for v in a), tuple(int(v) for v in b), 1, max(2, wpx))
    sealed = sm.astype(bool)
    if mode == 'fills':
        rgb = img.astype(np.int16)
        white = (rgb > P['white']).all(axis=2)
        free = ~white & ~sealed & roi
        for r, g, b, tol in P['exclude_colors']:
            free &= np.abs(rgb - np.array([r, g, b])).max(axis=2) > tol
    else:
        free = ~sealed & roi
    n, lab = cv2.connectedComponents(free.astype(np.uint8), connectivity=4)
    if mode == 'fills':
        # fill holes left by text and icons inside a colored block; blocks nested inside a hole
        # (the inside of an icon) are part of the block around them
        filled = lab.copy()
        objs0 = ndi.find_objects(lab)
        for i in range(1, n):
            sl = objs0[i - 1]
            if sl is None:
                continue
            comp = lab[sl] == i
            holes = ndi.binary_fill_holes(comp) & ~comp
            if holes.any():
                hl, hn = ndi.label(holes)
                for h in range(1, hn + 1):
                    hm = hl == h
                    inner = set(np.unique(lab[sl][hm]).tolist()) - {0}
                    ok = all(objs0[j - 1] is not None and _inside(objs0[j - 1], sl) and
                             not (lab[objs0[j - 1]] == j)[~_sub(hm, sl, objs0[j - 1])].any() for j in inner)
                    if ok:
                        filled[sl][hm] = i
        lab = filled
    border = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]]).tolist())
    if ctl.get('crop'):
        x0, y0, x1, y1 = [int(v) for v in ctl['crop']]
        e = lab[max(0, y0):y1, max(0, x0):x1]
        border |= set(np.unique(np.r_[e[0], e[-1], e[:, 0], e[:, -1]]).tolist())
    if mode == 'fills':
        border = set()  # in a diagram the blocks can touch the frame
    border.discard(0)

    objs = ndi.find_objects(lab)
    regions = {}
    dropped = {'edge': 0, 'small': 0, 'thin': 0, 'outside': 0, 'hand': 0}
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        if i in border:
            dropped['edge'] += 1
            continue
        m = lab[sl] == i
        area = m.sum() * mpp * mpp
        if area < P['min_room_m2']:
            dropped['small'] += 1
            continue
        dt = cv2.distanceTransform(np.pad(m, 1).astype(np.uint8), cv2.DIST_L2, 5)[1:-1, 1:-1]
        hw = dt.max() * mpp
        if hw < P['min_halfwidth_m']:
            dropped['thin'] += 1
            continue
        yy, xx = np.unravel_index(int(dt.argmax()), dt.shape)
        at = (xx + sl[1].start, yy + sl[0].start)
        g = apply(M, *at)
        if dist_to_shape(g, shape) > P['outside_m']:
            dropped['outside'] += 1
            continue
        regions[i] = {'sl': sl, 'area': area, 'hw': hw, 'at': at, 'words': []}

    for px in ctl.get('drop', []):
        i = region_at(lab, px, regions)
        if i in regions:
            del regions[i]
            dropped['hand'] += 1

    # labels -> regions
    unplaced = []
    near_px = max(2, int(1.0 / mpp))
    for w in words:
        cx, cy = (w['box'][0] + w['box'][2]) / 2, (w['box'][1] + w['box'][3]) / 2
        xi, yi = int(round(cx)), int(round(cy))
        if not (0 <= xi < W and 0 <= yi < H):
            continue
        i = int(lab[yi, xi])
        if i not in regions:
            win = lab[max(0, yi - near_px):yi + near_px + 1, max(0, xi - near_px):xi + near_px + 1]
            cand = [int(v) for v in np.unique(win) if int(v) in regions]
            i = cand[0] if len(cand) == 1 else None
        if i is None:
            if ROOMNUM.match(w['text'].upper()) or classify_text(w['text']):
                unplaced.append(w)
            continue
        regions[i]['words'].append(w)

    rooms = []
    flags = []
    setters = {}
    for s in ctl.get('set', []):
        i = region_at(lab, s['px'], regions)
        if i is None:
            say(f'  set at px {s["px"]}: no region there')
            continue
        setters.setdefault(i, {}).update({k: v for k, v in s.items() if k != 'px'})
    grow = P['grow_px'] if P['grow_px'] is not None else max(1, wpx // 2 + (1 if mode != 'fills' else 0))
    kgrow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
    eps = max(0.8, P['simplify_m'] / mpp)
    for i, R in regions.items():
        ws = R['words']
        if any(w.get('manual') for w in ws):
            ws = [w for w in ws if w.get('manual')]
        ws = text_lines(ws)
        text = ' '.join(w['text'] for w in ws)
        nums = []
        for w in ws:
            t = w['text'].upper().replace('O', '0') if re.fullmatch(r'[A-Z]?[0-9O]{3,5}[A-Z]?', w['text'].upper()) else w['text'].upper()
            if ROOMNUM.match(t) and re.search(r'\d{2}', t) and t not in nums:
                nums.append(t)
        r = {}
        kind = classify_text(text)
        if len(nums) == 1:
            r['ref'] = nums[0]
        elif len(nums) > 1:
            r['refs'] = nums
            r['flag'] = 'merged'
            flags.append(f'region at px {R["at"]} has {len(nums)} room numbers {nums}: rooms merged, add a cut')
        if not kind:
            if nums:
                kind = 'room'
            elif mode == 'fills':
                kind = 'room' if text else 'other'
            elif R['hw'] <= P['corridor_halfwidth_m'] and R['area'] >= 8 and R['area'] / (4 * R['hw'] ** 2) >= 3:
                kind = 'corridor'
            else:
                kind = 'other'
        words_only = ' '.join(w['text'] for w in ws if not ROOMNUM.match(w['text'].upper()))
        if r.get('ref'):
            r['label'] = r['ref']
        elif r.get('refs'):
            r['label'] = '/'.join(r['refs'])
        elif words_only and kind != 'corridor':
            r['label'] = words_only[:28]
        if words_only and (r.get('ref') or r.get('refs')):
            r['name'] = words_only[:40]
        r['kind'] = kind
        r.update(setters.get(i, {}))
        if r['kind'] not in FP.KINDS:
            raise SystemExit(f'unknown kind {r["kind"]}')
        # polygon
        sl = R['sl']
        pad = grow + 2
        ys, xs = slice(max(0, sl[0].start - pad), min(H, sl[0].stop + pad)), slice(max(0, sl[1].start - pad), min(W, sl[1].stop + pad))
        m = (lab[ys, xs] == i).astype(np.uint8)
        m = cv2.dilate(m, kgrow) & ~(roi[ys, xs] == 0)
        cs, hier = cv2.findContours(m.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        if not cs:
            continue
        k_out = max((k for k in range(len(cs)) if hier[0][k][3] < 0), key=lambda k: cv2.contourArea(cs[k]))
        ring = lambda c: [to_ll(float(p[0][0] + xs.start), float(p[0][1] + ys.start)) for p in cv2.approxPolyDP(c, eps, True)]
        r['poly'] = ring(cs[k_out])
        holes = [ring(cs[k]) for k in range(len(cs)) if hier[0][k][3] == k_out and cv2.contourArea(cs[k]) * mpp * mpp >= 1.0]
        holes = [h for h in holes if len(h) >= 3]
        if holes:
            r['holes'] = holes
        if len(r['poly']) < 3:
            continue
        r['at'] = to_ll(*R['at'])
        r['_i'] = i
        r['_area'] = round(R['area'], 1)
        rooms.append(r)

    # duplicates
    seen = {}
    for r in rooms:
        if r.get('ref'):
            seen.setdefault(r['ref'], []).append(r)
    for ref, rs in seen.items():
        if len(rs) > 1:
            flags.append(f'room {ref} labels {len(rs)} regions (areas {[x["_area"] for x in rs]} m2); kept the ref on the largest')
            rs.sort(key=lambda x: -x['_area'])
            for x in rs[1:]:
                x.pop('ref')
                x['flag'] = 'duplicate-label'

    # hand-placed elements
    for a in ctl.get('add', []):
        r = {k: v for k, v in a.items() if k not in ('px', 'pt')}
        if 'px' in a:
            r['poly'] = [to_ll(*q) for q in a['px']]
            if r.get('label') or r.get('ref'):
                cx = sum(q[0] for q in a['px']) / len(a['px'])
                cy = sum(q[1] for q in a['px']) / len(a['px'])
                r['at'] = to_ll(cx, cy)
        if 'pt' in a:
            r['at'] = to_ll(*a['pt'])
        r['hand'] = True
        rooms.append(r)

    # walls
    walls = []
    if mode != 'fills':
        for ln in sk_lines:
            if len(ln) < 2:
                continue
            c = np.array(ln, np.int32).reshape(-1, 1, 2)
            if cv2.arcLength(c, False) * mpp < P['min_wall_line_m']:
                continue
            ap = cv2.approxPolyDP(c, eps, False)
            walls.append([to_ll(float(p[0][0]), float(p[0][1])) for p in ap])
    wall_m = round(wpx * mpp, 2) if mode != 'fills' else None

    # QA against the building outline
    outside = 0.0
    for r in rooms:
        for q in r.get('poly') or [r['at']]:
            outside = max(outside, dist_to_shape(proj(*q), shape))
    kinds = {}
    for r in rooms:
        kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
    say(f'  wall thickness {wpx} px ({(wpx * mpp):.2f} m), {len(added)} door gaps sealed, {len(walls)} wall lines')
    say(f'  rooms: {len(rooms)} {kinds}; dropped {dropped}; refs: {sorted(r["ref"] for r in rooms if r.get("ref"))}')
    say(f'  traced area reaches {outside:.1f} m outside the building outline')
    for f in flags:
        say('  FLAG', f)
    for w in unplaced:
        say(f'  unplaced label "{w["text"]}" at px {[round(v) for v in w["box"][:2]]}')

    if debug:
        os.makedirs(debug, exist_ok=True)
        stem = os.path.join(debug, f'{FP.slug(code)}-{floor}')
        write_debug(stem, img, lab, rooms, regions, wall, sk, added, cps, M, res, outline_px, words)

    rec = {
        'format': FP.FORMAT,
        'building': code,
        'floor': floor,
        'title': ctl.get('title') or f'{bdoc["name"]}, floor {floor}',
        'extent': ctl.get('extent', 'full'),
        'source': ctl['source'],
        'how': ctl.get('how') or default_how(mode, ctl, cps),
        'notes': ctl.get('notes', []),
        'fit': {
            'transform': ctl.get('transform', 'similarity'), 'points': len(cps),
            'max_m': round(max(res), 2), 'rms_m': round(math.sqrt(sum(x * x for x in res) / len(res)), 2),
            'loo_max_m': round(max(loo), 2) if loo else None,
            'm_per_px': round(mpp, 4), 'rot_deg': round(rot, 2),
            'outside_outline_max_m': round(outside, 1),
            'control': [{'what': c['what'], 'px': c['px'], 'll': c['ll'], 'res_m': round(e, 2)} for c, e in zip(cps, res)],
        },
        'trace': {'mode': mode, 'tool': 'maps/trace_plan.py', 'date': datetime.date.today().isoformat(),
                  'plan_file': os.path.basename(path), 'page': page, 'dpi': dpi,
                  'params': {k: v for k, v in ctl.get('params', {}).items()},
                  'sealed_doors': len(added), 'dropped': dropped, 'flags': flags,
                  'unplaced_labels': [w['text'] for w in unplaced]},
        'wall_m': wall_m,
        'rooms': [{k: v for k, v in r.items() if not k.startswith('_')} for r in rooms],
        'walls': walls,
    }
    return rec


def default_how(mode, ctl, cps):
    what = {'walls': 'Walls are the dark lines of the plan image; rooms are the spaces they enclose, with door gaps closed by extending each wall end up to 2 m.',
            'vector': "Walls are the plan's own vector lines (from the PDF), rooms are the spaces they enclose, and room numbers are the PDF's text.",
            'fills': 'Rooms are the solid colored blocks of the map; labels were read by OCR and checked by hand.'}[mode]
    return (f'{what} The plan was fitted to the ground with a {ctl.get("transform", "similarity")} transform on {len(cps)} '
            f'control points ({"; ".join(c["what"] for c in cps)}).')


# ---------------------------------------------------------------------------
# debug overlays
# ---------------------------------------------------------------------------
KIND_BGR = {'room': (240, 200, 120), 'corridor': (200, 230, 200), 'stair': (60, 160, 240), 'elevator': (200, 120, 200),
            'restroom': (240, 160, 80), 'exit': (60, 200, 60), 'mech': (160, 160, 160), 'other': (190, 190, 190),
            'xbox': (200, 120, 200), 'pod': (120, 60, 120)}


def write_debug(stem, img, lab, rooms, regions, wall, sk, added, cps, M, res, outline_px, words):
    import cv2
    base = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    over = (base * 0.45 + 140).astype(np.uint8)
    col = np.zeros_like(over)
    for r in rooms:
        i = r.get('_i')
        if i:
            col[lab == i] = KIND_BGR[r['kind']]
    m = col.any(axis=2)
    over[m] = (over[m] * 0.4 + col[m] * 0.6).astype(np.uint8)
    over[wall] = (40, 40, 40)
    over[sk] = (0, 0, 220)
    for a, b in added:
        cv2.line(over, a, b, (255, 80, 0), 2)
    for p in outline_px:
        cv2.polylines(over, [np.round(p).astype(np.int32)], True, (0, 140, 255), 2)
    fs = max(0.4, img.shape[1] / 3000)
    for r in rooms:
        i = r.get('_i')
        if not i:
            continue
        x, y = regions[i]['at']
        txt = f"{r.get('label') or ''} [{r['kind'][:4]}]" + (' !' + r['flag'] if r.get('flag') else '')
        cv2.putText(over, txt, (int(x) - 20, int(y)), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 180) if r.get('flag') else (20, 20, 20), 1, cv2.LINE_AA)
    for c, e in zip(cps, res):
        x, y = [int(v) for v in c['px']]
        cv2.drawMarker(over, (x, y), (0, 160, 0), cv2.MARKER_CROSS, 24, 2)
        cv2.putText(over, f'{e:.2f} m', (x + 6, y - 6), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 120, 0), 1, cv2.LINE_AA)
    cv2.imwrite(stem + '-trace.png', over)
    ocr = base.copy()
    for w in words:
        b = [int(v) for v in w['box']]
        cv2.rectangle(ocr, (b[0], b[1]), (b[2], b[3]), (0, 0, 255), 1)
        cv2.putText(ocr, w['text'], (b[0], b[1] - 2), cv2.FONT_HERSHEY_SIMPLEX, fs * 0.8, (255, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(stem + '-labels.png', ocr)
    say(f'  debug: {stem}-trace.png, {stem}-labels.png')


def render_grid(path, page, dpi, out, step=100):
    import cv2
    img = cv2.cvtColor(load_raster(path, page, dpi), cv2.COLOR_RGB2BGR)
    H, W = img.shape[:2]
    for x in range(0, W, step):
        cv2.line(img, (x, 0), (x, H), (255, 160, 160), 1)
        cv2.putText(img, str(x), (x + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 0, 0), 1)
    for y in range(0, H, step):
        cv2.line(img, (0, y), (W, y), (255, 160, 160), 1)
        cv2.putText(img, str(y), (2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 0, 0), 1)
    cv2.imwrite(out, img)
    say(f'{out}: {W}x{H} px')


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------
def write_record(rec, name=None):
    pdir = os.path.join(HERE, 'plans')
    os.makedirs(pdir, exist_ok=True)
    fn = name or f'{FP.slug(rec["building"])}-{rec["floor"]}.json'
    with open(os.path.join(pdir, fn), 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, separators=(',', ':'))
    say(f'  wrote maps/plans/{fn}')


def merge_all(codes=None):
    recs = FP.load_records(os.path.join(HERE, 'plans'))
    for code in sorted(set(recs) | set(codes or [])):
        bfile = os.path.join(HERE, FP.slug(code) + '.json')
        if not os.path.exists(bfile):
            say(f'no maps/{FP.slug(code)}.json for {code}')
            continue
        with open(bfile, encoding='utf-8') as fh:
            doc = json.load(fh)
        FP.merge_into(doc, recs.get(code, []))
        with open(bfile, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, ensure_ascii=False, separators=(',', ':'))
        say(f'  merged {len(recs.get(code, []))} floor record(s) into maps/{FP.slug(code)}.json')
    FP.write_plans_md(os.path.join(HERE, 'SOURCES.md'), recs, datetime.date.today().isoformat())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('control', nargs='*', help='control file(s)')
    ap.add_argument('--debug', help='write overlay PNGs here')
    ap.add_argument('--dry-run', action='store_true', help="don't write anything but the debug images")
    ap.add_argument('--render', help='rasterize this plan with a pixel grid (for picking control points)')
    ap.add_argument('--page', type=int, default=1)
    ap.add_argument('--dpi', type=float, default=150)
    ap.add_argument('--out', help='output PNG for --render')
    ap.add_argument('--merge-all', action='store_true', help='re-merge every floor record into maps/<CODE>.json')
    a = ap.parse_args()
    if a.render:
        render_grid(a.render, a.page, a.dpi, a.out or 'grid.png')
        return
    touched = set()
    for cp in a.control:
        with open(cp, encoding='utf-8') as fh:
            ctl = json.load(fh)
        rec = trace(ctl, cp, a.debug)
        if not a.dry_run:
            write_record(rec, ctl.get('out'))
            touched.add(rec['building'])
    if (touched or a.merge_all) and not a.dry_run:
        merge_all(touched)


if __name__ == '__main__':
    main()
