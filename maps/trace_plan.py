#!/usr/bin/env python3
"""Trace a published floor plan (image or PDF page) into georeferenced rooms and walls.

    python maps/trace_plan.py maps/plans/YRL-1.control.json          # trace, write the floor record, merge into maps/YRL.json
    python maps/trace_plan.py CONTROL --debug DIR                     # also write overlay PNGs to check the trace
    python maps/trace_plan.py CONTROL --dry-run --debug DIR           # trace and report only
    python maps/trace_plan.py --render PLAN.pdf --page 2 --dpi 150 --out grid.png
                                                                      # rasterize with a pixel grid, to pick control points
    python maps/trace_plan.py --merge-all                             # re-merge every floor record into maps/<CODE>.json
    python maps/trace_plan.py --regularize-all                        # snap every record's rooms and walls to its plan grid (done on every write too)

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
    'label_boxes': False,   # labels sit in small white boxes (UCLA Space Inventory): OCR each box, then erase it
    'label_box_max': None,  # label_boxes: only erase a white box at most this many times the text's width and height
    'outside_colors': [],   # [[r, g, b, tol], ...] colors that are outside the building (background, courtyards)
    'hatch_m': 0,           # > 0: dense hatching (stair treads) in blocks wider than this is opened up into floor
    'ocr_rotated': True,    # also read text turned 90 degrees
}
# Space Inventory style suffixes on room numbers
SUFFIX_KIND = [('corridor', re.compile(r'\d(CO|CU|CR|LO|VE)$')), ('stair', re.compile(r'\dST$')), ('elevator', re.compile(r'\dEL$'))]

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


def box_labels(img, mpp_guess=None):
    """White label boxes (UCLA Space Inventory style): find them, OCR each one on its own."""
    import cv2
    import pytesseract
    white = (img > 228).all(axis=2).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(white, connectivity=4)
    out = []
    cfg = '--psm 7 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (6 <= min(w, h) <= 40 and 14 <= max(w, h) <= 160) or a < 0.7 * w * h:
            continue
        crop = img[max(0, y - 1):y + h + 1, max(0, x - 1):x + w + 1]
        g = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        g = cv2.resize(g, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        g = cv2.copyMakeBorder(g, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        cands = [g] if w >= h else [cv2.rotate(g, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(g, cv2.ROTATE_90_COUNTERCLOCKWISE)]
        best = None
        for c in cands:
            d = pytesseract.image_to_data(c, config=cfg, output_type=pytesseract.Output.DICT)
            txt = ''.join(t for t in d['text'] if t.strip())
            confs = [float(v) for v, t in zip(d['conf'], d['text']) if t.strip()]
            conf = min(confs) if confs else -1
            if txt and ROOMNUM.match(txt):
                conf += 50
            if txt and (best is None or conf > best[1]):
                best = (txt, conf)
        if best:
            out.append({'text': best[0], 'conf': best[1], 'box': [float(x), float(y), float(x + w), float(y + h)], 'labelbox': True})
    return out


_RAPID = None


def rapid_words(img, min_conf=0.5):
    """Words from RapidOCR (pip install rapidocr-onnxruntime), which reads photos far better than tesseract.
    None when it isn't installed."""
    global _RAPID
    try:
        if _RAPID is None:
            from rapidocr_onnxruntime import RapidOCR
            _RAPID = RapidOCR()
    except Exception:
        return None
    res, _ = _RAPID(img[..., ::-1].copy())
    out = []
    for box, txt, conf in res or []:
        txt = re.sub(r'[^A-Za-z0-9&/.,\- ]', '', txt).strip()
        if float(conf) < min_conf or not re.search(r'[A-Za-z0-9]', txt):
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append({'text': txt.strip(), 'conf': float(conf) * 100, 'box': [min(xs), min(ys), max(xs), max(ys)]})
    return out


def rapid_words_all(img, rotated=True, scale=1.0):
    """RapidOCR on the image and (for labels turned 90 degrees) on it turned both ways; None if not installed."""
    import cv2
    src = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else img
    ws = rapid_words(src)
    if ws is None:
        return None
    H, W = src.shape[:2]
    if rotated:
        for rot, back in ((cv2.ROTATE_90_CLOCKWISE, lambda b: [b[1], H - b[2], b[3], H - b[0]]),
                          (cv2.ROTATE_90_COUNTERCLOCKWISE, lambda b: [W - b[3], b[0], W - b[1], b[2]])):
            for w in rapid_words(cv2.rotate(src, rot)) or []:
                w['box'] = back(w['box'])
                bw, bh = w['box'][2] - w['box'][0], w['box'][3] - w['box'][1]
                if bh > 1.5 * bw and not any(_iou(w['box'], o['box']) > 0.1 for o in ws):
                    w['rotated'] = True
                    ws.append(w)
    for w in ws:
        w['box'] = [v / scale for v in w['box']]
    return ws


def spaced_text(img, box, pad=6):
    """Tesseract on one text block, to get the spaces RapidOCR drops ("TOMOLECULARSCI")."""
    import cv2
    import pytesseract
    x0, y0, x1, y1 = [int(v) for v in box]
    c = img[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_RGB2GRAY), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    try:
        return ' '.join(pytesseract.image_to_string(g, config='--psm 6').split())
    except Exception:
        return ''


def rotated_words(img, scale, conf):
    """OCR on the image turned 90 degrees both ways, boxes mapped back."""
    import cv2
    H, W = img.shape[:2]
    out = []
    for rot, back in ((cv2.ROTATE_90_CLOCKWISE, lambda b: [b[1], H - b[2], b[3], H - b[0]]),
                      (cv2.ROTATE_90_COUNTERCLOCKWISE, lambda b: [W - b[3], b[0], W - b[1], b[2]])):
        for w in ocr_words(cv2.rotate(img, rot), scale, conf):
            if len(w['text']) < 3:
                continue
            w['box'] = back(w['box'])
            w['rotated'] = True
            out.append(w)
    return out


# ---------------------------------------------------------------------------
# photos of posted evacuation plans
# ---------------------------------------------------------------------------
def rectify(img, corners=None, scale=2.0, thr=90):
    """Find the sign's rectangular frame (or use 4 given corners: TL, TR, BR, BL) and undo the perspective."""
    import cv2
    H, W = img.shape[:2]
    if corners is None:
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        m = cv2.morphologyEx((g < thr).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        cs, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        cands = []
        for c in cs:
            hull = cv2.convexHull(c)
            a = cv2.contourArea(hull)
            if a < 0.05 * W * H or cv2.contourArea(c) / a < 0.95:
                continue
            peri = cv2.arcLength(hull, True)
            q = None
            for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
                q = cv2.approxPolyDP(hull, eps * peri, True)
                if len(q) == 4:
                    break
            if len(q) == 4:
                cands.append((a, c, q.reshape(-1, 2).astype(float)))
        if not cands:
            raise SystemExit('could not find the sign frame; give its 4 corners as plan.corners [[x,y] TL, TR, BR, BL] in photo pixels')
        a, c, q = min(cands, key=lambda t: t[0])     # the inner edge of the frame
        sm, df = q.sum(1), q[:, 0] - q[:, 1]
        q = np.array([q[sm.argmin()], q[df.argmax()], q[sm.argmax()], q[df.argmin()]])
        # a straight line through each side, ignoring the rounded corners
        P = c.reshape(-1, 2).astype(float)
        lines = []
        for i in range(4):
            A, B = q[i], q[(i + 1) % 4]
            L = np.linalg.norm(B - A)
            u = (B - A) / L
            nrm = np.array([-u[1], u[0]])
            t, dd = (P - A) @ u, np.abs((P - A) @ nrm)
            sel = P[(t > 0.08 * L) & (t < 0.92 * L) & (dd < 0.02 * L)]
            if len(sel) < 10:
                lines.append((A, u))
                continue
            vx, vy, x0, y0 = cv2.fitLine(sel.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
            lines.append((np.array([x0, y0]), np.array([vx, vy])))

        def inter(l1, l2):
            (p0, u0), (p1, u1) = l1, l2
            t = np.linalg.solve(np.array([u0, -u1]).T, p1 - p0)
            return p0 + t[0] * u0
        corners = [inter(lines[3], lines[0]), inter(lines[0], lines[1]), inter(lines[1], lines[2]), inter(lines[2], lines[3])]
    C = np.array(corners, float)
    w = (np.linalg.norm(C[1] - C[0]) + np.linalg.norm(C[2] - C[3])) / 2 * scale
    h = (np.linalg.norm(C[3] - C[0]) + np.linalg.norm(C[2] - C[1])) / 2 * scale
    w, h = int(round(w)), int(round(h))
    T = cv2.getPerspectiveTransform(C.astype(np.float32), np.float32([[0, 0], [w, 0], [w, h], [0, h]]))
    out = cv2.warpPerspective(img, T, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return out, [[round(float(x), 1), round(float(y), 1)] for x, y in C]


def flatten(gray, sigma=None):
    """Even out glare and shading: divide by a blurred copy of the bright paper."""
    import cv2
    H, W = gray.shape
    sigma = sigma or max(H, W) / 40
    bg = cv2.GaussianBlur(cv2.dilate(gray, np.ones((15, 15), np.uint8)), (0, 0), sigma)
    return np.clip(gray.astype(float) / np.maximum(bg, 1) * 230, 0, 255).astype(np.uint8)


def find_xboxes(wall, mpp, min_m=0.8, max_m=4.0):
    """Crossed boxes (the plan symbol for an elevator): two diagonals crossing at their midpoints."""
    import cv2
    from skimage.morphology import skeletonize
    sk = skeletonize(wall).astype(np.uint8) * 255
    L = cv2.HoughLinesP(sk, 1, np.pi / 180, threshold=12, minLineLength=int(min_m * 1.2 / mpp), maxLineGap=3)
    if L is None:
        return []
    diag = []
    for x1, y1, x2, y2 in L.reshape(-1, 4):
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180
        ln = math.hypot(x2 - x1, y2 - y1)
        if ln * mpp > max_m * 1.5:
            continue
        if 30 < ang < 60:
            diag.append(('p', (x1 + x2) / 2, (y1 + y2) / 2, ln))
        elif 120 < ang < 150:
            diag.append(('n', (x1 + x2) / 2, (y1 + y2) / 2, ln))
    boxes = []
    for a in diag:
        if a[0] != 'p':
            continue
        for b in diag:
            if b[0] == 'n' and abs(a[3] - b[3]) < 0.35 * max(a[3], b[3]) and math.hypot(a[1] - b[1], a[2] - b[2]) < 0.2 * max(a[3], b[3]):
                cx, cy, half = (a[1] + b[1]) / 2, (a[2] + b[2]) / 2, max(a[3], b[3]) / 2 / math.sqrt(2)
                if min_m <= 2 * half * mpp <= max_m and not any(math.hypot(cx - o[0], cy - o[1]) < half for o in boxes):
                    boxes.append((cx, cy, half))
    return boxes


def text_blocks(words, gap=1.6):
    """Group OCR words into blocks (a sign label like "STAIR 1 / ROOF / ACCESS")."""
    blocks = []
    for w in sorted(words, key=lambda w: (w['box'][1], w['box'][0])):
        h = w['box'][3] - w['box'][1]
        for b in blocks:
            bx = b['box']
            if (w['box'][0] < bx[2] + gap * h and w['box'][2] > bx[0] - gap * h and
                    w['box'][1] < bx[3] + 0.9 * h and w['box'][3] > bx[1] - 0.9 * h):
                b['words'].append(w)
                b['box'] = [min(bx[0], w['box'][0]), min(bx[1], w['box'][1]), max(bx[2], w['box'][2]), max(bx[3], w['box'][3])]
                break
        else:
            blocks.append({'words': [w], 'box': list(w['box'])})
    for b in blocks:
        b['text'] = ' '.join(w['text'] for w in text_lines(b['words']))
    return blocks


def region_text(gray, mask, scale=4):
    """OCR one room on its own: everything outside the room is blanked first."""
    import cv2
    import pytesseract
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return ''
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    from scipy import ndimage as ndi
    g = gray[y0:y1, x0:x1].copy()
    m = cv2.erode(ndi.binary_fill_holes(mask[y0:y1, x0:x1]).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    g[~m] = 255
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    g = cv2.copyMakeBorder(g, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    _, g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cfg = '--psm 6 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    try:
        txt = pytesseract.image_to_string(g, config=cfg)
    except Exception:
        return ''
    return ' '.join(t for t in txt.split() if re.search(r'[A-Za-z0-9]', t))


# ---------------------------------------------------------------------------
# georeferencing
# ---------------------------------------------------------------------------
def _seg_closest(P, A, B):
    """closest points on segments A->B (M,2) to points P (N,2): (N,2) points and distances."""
    d = B - A
    L = (d ** 2).sum(1)
    L[L == 0] = 1e-12
    t = np.clip(((P[:, None, :] - A[None]) * d[None]).sum(2) / L[None], 0, 1)
    Q = A[None] + t[..., None] * d[None]
    dist = np.linalg.norm(P[:, None, :] - Q, axis=2)
    k = dist.argmin(1)
    return Q[np.arange(len(P)), k], dist[np.arange(len(P)), k]


def _fit_sim(U, X):
    rows, rhs = [], []
    for (u, v), (x, y) in zip(U, X):
        rows += [[u, v, 1, 0], [-v, u, 0, 1]]
        rhs += [x, y]
    a, b, tx, ty = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
    return np.array([[a, b, tx], [b, -a, ty], [0, 0, 1]])


def _fit_sim_fixed(U, X, th):
    c, sn = math.cos(th), math.sin(th)
    rows, rhs = [], []
    for (u, v), (x, y) in zip(U, X):
        rows += [[c * u + sn * v, 1, 0], [sn * u - c * v, 0, 1]]
        rhs += [x, y]
    s_, tx, ty = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
    return np.array([[s_ * c, s_ * sn, tx], [s_ * sn, -s_ * c, ty], [0, 0, 1]])


def outline_icp(foot, shape, rot0=0.0, trim=0.85, fixed=False, search=False, one_sided=False):
    """Fit the plan's footprint (bool mask) to the building outline (projected metres) by ICP."""
    import cv2
    cs, _ = cv2.findContours(foot.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    pts = np.concatenate([c[:, 0, :] for c in cs if len(c) > 20]).astype(float)
    step = max(1, len(pts) // 2500)
    U = pts[::step]
    segs = [(r[i - 1], r[i]) for o, holes in shape for r in [o] + holes for i in range(len(r))]
    A = np.array([a for a, _ in segs], float)
    B = np.array([b for _, b in segs], float)
    area_px = foot.sum()
    area_m = sum(abs(_area(o)) - sum(abs(_area(h)) for h in holes) for o, holes in shape)
    s0 = math.sqrt(area_m / area_px)
    cu = np.argwhere(foot)[:, ::-1].mean(0)
    allpts = np.array([q for o, _ in shape for q in o])
    cx = allpts.mean(0)
    best = None
    starts = []
    for rot in (rot0 if isinstance(rot0, list) else [rot0] if rot0 is not None else [0, 90, 180, 270]):
        if not search:
            starts.append((rot, s0, cx))
            continue
        # a plan of one wing: try smaller scales and several places on the outline
        x0_, y0_ = allpts.min(0)
        x1_, y1_ = allpts.max(0)
        for f in (0.35, 0.5, 0.7, 1.0):
            for gx in (0.2, 0.5, 0.8):
                for gy in (0.2, 0.5, 0.8):
                    starts.append((rot, s0 * f, np.array([x0_ + gx * (x1_ - x0_), y0_ + gy * (y1_ - y0_)])))
    for rot, sc, cxy in starts:
        th = math.radians(rot)
        a, b = sc * math.cos(th), sc * math.sin(th)
        M = np.array([[a, b, 0], [b, -a, 0], [0, 0, 1.0]])
        M[0, 2], M[1, 2] = cxy[0] - (a * cu[0] + b * cu[1]), cxy[1] - (b * cu[0] - a * cu[1])
        for _ in range(60):
            Xp = (M[:2, :2] @ U.T).T + M[:2, 2]
            Q, d = _seg_closest(Xp, A, B)
            keep = d <= np.quantile(d, trim)
            M = _fit_sim_fixed(U[keep], Q[keep], th) if fixed else _fit_sim(U[keep], Q[keep])
        Xp = (M[:2, :2] @ U.T).T + M[:2, 2]
        _, d = _seg_closest(Xp, A, B)
        med = float(np.median(d))
        sc_fin = math.sqrt(abs(np.linalg.det(M[:2, :2])))
        if not np.isfinite(med) or sc_fin < 0.15 * s0 or sc_fin > 6 * s0:
            continue  # collapsed onto one edge (scale ran away): not a fit
        if best is None or med < best[1]:
            best = (M, med, float(np.quantile(d, 0.9)), d)
    return best


def _edge_positions(polys, horizontal, min_len=2.0, tol=1.0):
    """Positions of the long axis-parallel edges of rectilinear polygons (already rotated to the building grid)."""
    vals = []
    for ring in polys:
        for i in range(len(ring)):
            (x1, y1), (x2, y2) = ring[i - 1], ring[i]
            dx, dy = x2 - x1, y2 - y1
            L = math.hypot(dx, dy)
            if L < min_len:
                continue
            if horizontal and abs(dy) < 0.15 * abs(dx):
                vals.append(((y1 + y2) / 2, L))
            if not horizontal and abs(dx) < 0.15 * abs(dy):
                vals.append(((x1 + x2) / 2, L))
    vals.sort()
    out = []
    for v, w in vals:
        if out and v - out[-1][0] < tol:
            pv, pw = out[-1]
            out[-1] = ((pv * pw + v * w) / (pw + w), pw + w)
        else:
            out.append((v, w))
    return [v for v, _ in out]


def _align(a, b, tol):
    """Order-preserving matching of two sorted lists (dynamic programming)."""
    n, m = len(a), len(b)
    S = np.zeros((n + 1, m + 1))
    B = {}
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, how = S[i - 1, j], 'a'
            if S[i, j - 1] > best:
                best, how = S[i, j - 1], 'b'
            d = abs(a[i - 1] - b[j - 1])
            if d < tol and S[i - 1, j - 1] + 1 - d / tol > best:
                best, how = S[i - 1, j - 1] + 1 - d / tol, 'm'
            S[i, j], B[(i, j)] = best, how
    i, j, pairs = n, m, []
    while i > 0 and j > 0:
        h = B[(i, j)]
        if h == 'm':
            pairs.append((a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif h == 'a':
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


class AxisWarp:
    """Sign -> ground: a similarity, then a piecewise-linear stretch along each axis of the building grid,
    so a schematic sign (wings drawn narrower than they are) still lands on the building's walls."""

    def __init__(self, M, sign_polys_px, shape, tol=8.0):
        self.M = M
        ang = []
        for o, holes in shape:
            for r in [o] + holes:
                for i in range(len(r)):
                    (x1, y1), (x2, y2) = r[i - 1], r[i]
                    L = math.hypot(x2 - x1, y2 - y1)
                    if L > 2:
                        ang.append((math.atan2(y2 - y1, x2 - x1) % (math.pi / 2), L))
        # dominant grid angle (circular mean on a 90 degree period)
        c = sum(L * math.cos(4 * t) for t, L in ang)
        s_ = sum(L * math.sin(4 * t) for t, L in ang)
        self.th = math.atan2(s_, c) / 4
        rot = lambda P, t: [(x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t)) for x, y in P]
        self._rot = rot
        S = [rot([apply(M, u, v) for u, v in ring], -self.th) for ring in sign_polys_px]
        O = [rot(r, -self.th) for o, holes in shape for r in [o] + holes]
        self.px = self._pairs(S, O, 0, tol)
        self.py = self._pairs(S, O, 1, tol)

    @staticmethod
    def _pairs(S, O, k, tol):
        # first line up the overall extents, then match the edges in between in order
        s0, s1 = min(p[k] for r in S for p in r), max(p[k] for r in S for p in r)
        o0, o1 = min(p[k] for r in O for p in r), max(p[k] for r in O for p in r)
        a = (o1 - o0) / (s1 - s0)
        es, eo = _edge_positions(S, k == 1), _edge_positions(O, k == 1)
        m = _align([o0 + a * (v - s0) for v in es], eo, tol)
        inv = {round(o0 + a * (v - s0), 6): v for v in es}
        pairs = [(inv[round(p, 6)], q) for p, q in m]
        pairs = [(s0, o0)] + [pq for pq in pairs if s0 + 0.5 < pq[0] < s1 - 0.5] + [(s1, o1)]
        out = []
        for p, q in pairs:   # keep it monotonic
            if not out or (p > out[-1][0] + 0.3 and q > out[-1][1] + 0.3):
                out.append((p, q))
        return out

    @staticmethod
    def _pl(pairs, v, inv=False):
        if len(pairs) < 2:
            return v + ((pairs[0][1] - pairs[0][0]) * (-1 if inv else 1) if pairs else 0)
        xs = [q if inv else p for p, q in pairs]
        ys = [p if inv else q for p, q in pairs]
        if v <= xs[0]:
            return ys[0] + (v - xs[0])
        if v >= xs[-1]:
            return ys[-1] + (v - xs[-1])
        return float(np.interp(v, xs, ys))

    def xy(self, u, v):
        x, y = apply(self.M, u, v)
        (x, y), = self._rot([(x, y)], -self.th)
        x, y = self._pl(self.px, x), self._pl(self.py, y)
        (x, y), = self._rot([(x, y)], self.th)
        return x, y

    def inv(self, X, Y):
        (x, y), = self._rot([(X, Y)], -self.th)
        x, y = self._pl(self.px, x, True), self._pl(self.py, y, True)
        (x, y), = self._rot([(x, y)], self.th)
        return apply(np.linalg.inv(self.M), x, y)


def _area(r):
    return sum(r[i - 1][0] * r[i][1] - r[i][0] * r[i - 1][1] for i in range(len(r))) / 2
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
NUMWORD = re.compile(r'[A-Z]{0,2}\d{3,5}[A-Z]{0,2}|[A-Z]{1,2}\d{2}')


def ndi_fill(m):
    from scipy import ndimage as ndi
    return ndi.binary_fill_holes(m)


def ndi_label(m):
    from scipy import ndimage as ndi
    return ndi.label(m)


def split_by_labels(lab, regions, words, mpp, min_m2, near_px, on='numbers'):
    """A region holding two or more room numbers (or, with on='text', named labels) is several spaces
    joined through door gaps: split it by watershed from the labels (it cuts at the narrowest places, the doorways)."""
    import cv2
    from skimage.segmentation import watershed
    byreg = {}
    if on == 'text':
        words = [{'text': b['text'], 'box': b['box'], 'members': b['words']} for b in text_blocks(words, gap=0.8)
                 if NUMWORD.search(b['text'].upper()) or len(re.sub(r'[^A-Za-z]', '', b['text'])) >= 4]
    for w in words:
        if on != 'text' and not NUMWORD.fullmatch(w['text'].upper().replace(' ', '')):
            continue
        cx_, cy_ = int((w['box'][0] + w['box'][2]) / 2), int((w['box'][1] + w['box'][3]) / 2)
        i = region_at(lab, (cx_, cy_), regions, r=max(near_px, int((w['box'][3] - w['box'][1]) / 2)))
        if i is not None:
            if lab[cy_, cx_] != i:   # the seed must sit on the region: nearest pixel of it
                win = lab[max(0, cy_ - 40):cy_ + 41, max(0, cx_ - 40):cx_ + 41] == i
                ys_, xs_ = np.nonzero(win)
                if not len(ys_):
                    continue
                k_ = int(np.argmin((ys_ - min(40, cy_)) ** 2 + (xs_ - min(40, cx_)) ** 2))
                cy_, cx_ = max(0, cy_ - 40) + int(ys_[k_]), max(0, cx_ - 40) + int(xs_[k_])
            byreg.setdefault(i, []).append((cx_, cy_, w['text'].upper(), w.get('members', [w])))
    nxt = int(lab.max()) + 1
    nsplit = 0
    for i, seeds in byreg.items():
        if len(seeds) < 2 or i not in regions:
            continue
        sl = regions[i]['sl']
        m = lab[sl] == i
        dt = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 5)
        mk = np.zeros(m.shape, np.int32)
        for k, (sx, sy, _, _) in enumerate(seeds, start=1):
            mk[max(0, sy - sl[0].start - 2):sy - sl[0].start + 3, max(0, sx - sl[1].start - 2):sx - sl[1].start + 3] = k
        # unlabeled floor far from every number (a corridor run) gets its own shared seed,
        # so a small labeled space (an elevator) can't flood the whole corridor through its door
        far = 3.5 / mpp
        sd = np.ones(m.shape, np.uint8)
        for sx, sy, _, _ in seeds:
            sd[min(m.shape[0] - 1, max(0, sy - sl[0].start)), min(m.shape[1] - 1, max(0, sx - sl[1].start))] = 0
        dseed = cv2.distanceTransform(sd, cv2.DIST_L2, 5)
        # only in corridor-like strips (a hall's far corners belong to the hall)
        free_seed = m & (dseed > far) & (dt >= 0.6 / mpp) & (dt <= 1.6 / mpp)
        if free_seed.any():
            n_fs, fs_lab = cv2.connectedComponents(free_seed.astype(np.uint8), connectivity=8)
            sizes = np.bincount(fs_lab.ravel())
            free_seed &= sizes[fs_lab] * mpp * mpp >= 4.0   # long enough to be a corridor run
        mk[free_seed] = len(seeds) + 1
        mk[~m] = 0
        ws = watershed(-dt, mk, mask=m)
        fk = len(seeds) + 1
        if (ws == fk).any():
            # each unlabeled piece goes back to the labeled neighbour that wraps most of it (a big room's far end);
            # a piece that no single space wraps (a corridor run) stays a space of its own
            fl, fn = ndi_label(ws == fk)
            nk = fk + 1
            for c in range(1, fn + 1):
                piece = fl == c
                ring = cv2.dilate(piece.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~piece & m
                best, bn = None, 0
                for k, (_, _, t, _) in enumerate(seeds, start=1):
                    if re.search(r'(EL|ST)$', t):
                        continue
                    cnt = int((ring & (ws == k)).sum())
                    if cnt > bn:
                        best, bn = k, cnt
                if best and bn > 0.5 * ring.sum():
                    ws[piece] = best
                else:
                    ws[piece] = nk
                    nk += 1
        base = regions.pop(i)
        nsplit += 1
        for k in range(1, int(ws.max()) + 1):
            part = ws == k
            if part.sum() * mpp * mpp < min_m2:
                continue
            lab[sl][part] = nxt
            pdt = cv2.distanceTransform(np.pad(part, 1).astype(np.uint8), cv2.DIST_L2, 5)[1:-1, 1:-1]
            yy, xx = np.unravel_index(int(pdt.argmax()), pdt.shape)
            ys_, xs_ = np.nonzero(part)
            r = dict(base)
            r.update({'sl': (slice(sl[0].start + ys_.min(), sl[0].start + ys_.max() + 1), slice(sl[1].start + xs_.min(), sl[1].start + xs_.max() + 1)),
                      'area': part.sum() * mpp * mpp, 'hw': pdt.max() * mpp, 'at': (xx + sl[1].start, yy + sl[0].start), 'split': True})
            if 'words' in r:
                r['words'] = []
            if k <= len(seeds):
                for mw in seeds[k - 1][3]:
                    mw['_region'] = nxt
            regions[nxt] = r
            nxt += 1
    return nsplit


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

    shape = [([proj(*q) for q in p['outer']], [[proj(*q) for q in h] for h in p['holes']]) for p in bdoc['outline']['polys']]
    roi = np.ones((H, W), bool)
    if ctl.get('crop'):
        x0, y0, x1, y1 = [int(v) for v in ctl['crop']]
        roi[:] = False
        roi[max(0, y0):y1, max(0, x0):x1] = True
    outside = np.zeros((H, W), bool)
    for r, g, b, tol in P['outside_colors']:
        outside |= (np.abs(img.astype(np.int16) - np.array([r, g, b])).max(axis=2) <= tol)

    # georeference
    icp = None
    early_words = None
    if isinstance(ctl['control'], dict) and ctl['control'].get('auto') == 'stack':
        # stairs and elevators stack floor over floor: line their labels up with a floor that's already traced
        early_words = rapid_words_all(img, P['ocr_rotated'], P.get('ocr_upscale', 1.0)) or []
        ref_floor = str(ctl['control']['ref_floor'])
        recs = [r for r in FP.load_records(os.path.join(HERE, 'plans')).get(code, []) if r['floor'] == ref_floor]
        if not recs:
            raise SystemExit(f'stack: no traced floor {ref_floor} for {code} yet')
        pat = re.compile(ctl['control'].get('pattern', r'^\d(\d{3}(?:EL|ST))$'))
        refpts = {}
        for r in recs[0]['rooms']:
            mm = pat.match(r.get('ref') or '')
            if mm and r.get('poly'):
                refpts[mm.group(1)] = FP.room_point(r)
        ctl['control'] = []
        seen = set()
        for w in early_words:
            mm = pat.match(w['text'].upper().replace(' ', ''))
            if mm and mm.group(1) in refpts and mm.group(1) not in seen:
                seen.add(mm.group(1))
                ctl['control'].append({'px': [(w['box'][0] + w['box'][2]) / 2, (w['box'][1] + w['box'][3]) / 2],
                                       'll': refpts[mm.group(1)], 'what': f'{w["text"]} over floor {ref_floor} {mm.group(1)}'})
        # drop labels that don't fit the rest (a misread number, a core that moved)
        while len(ctl['control']) > 3:
            cps_ = resolve_control(ctl['control'], bdoc)
            _, res_, _, _, _ = georef(cps_, 'similarity')
            if max(res_) <= 2.5:
                break
            bad = int(np.argmax(res_))
            say(f'  stack: dropping {ctl["control"][bad]["what"]} ({res_[bad]:.1f} m off)')
            ctl['control'].pop(bad)
        say(f'  stack: {len(ctl["control"])} stair/elevator labels match floor {ref_floor}')
        if len(ctl['control']) < 2:
            raise SystemExit('stack: fewer than 2 matching stair/elevator labels')
    if isinstance(ctl['control'], dict) and ctl['control'].get('auto') == 'outline':
        foot = roi & ~outside & ~(img > 245).all(axis=2)
        foot = cv2.morphologyEx(foot.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n_, lab_, st_, _ = cv2.connectedComponentsWithStats(foot, connectivity=8)
        big = 1 + int(np.argmax(st_[1:, cv2.CC_STAT_AREA]))
        foot = lab_ == big
        if foot.sum() < 0.4 * (st_[big, cv2.CC_STAT_WIDTH] * st_[big, cv2.CC_STAT_HEIGHT]):
            # line drawing on white: the footprint is what the walls enclose (doorways closed first)
            kk = max(5, int(0.012 * max(H, W))) | 1
            foot = ndi_fill(cv2.morphologyEx(foot.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kk, kk))))
        cv2.imwrite(os.path.join(HERE, '.cache', f'foot-{FP.slug(code)}-{floor}.png'), (foot * 255).astype(np.uint8))
        M, med, p90, dists = outline_icp(foot, shape, ctl['control'].get('rot_init', 0.0), P.get('icp_trim', 0.85),
                                         ctl['control'].get('fix_rotation', False), ctl['control'].get('search', False))
        mpp = math.sqrt(abs(np.linalg.det(M[:2, :2])))
        rot = math.degrees(math.atan2(M[1, 0], M[0, 0]))
        icp = {'median_m': round(med, 2), 'p90_m': round(p90, 2)}
        cps, res, loo = [], [med], []
        say(f'  fit: plan footprint fitted to the building outline (ICP): median {med:.2f} m, 90% within {p90:.2f} m, '
            f'{mpp * 100:.1f} cm/px, rotation {rot:.1f} deg')
    else:
        cps = resolve_control(ctl['control'], bdoc)
        M, res, loo, mpp, rot = georef(cps, ctl.get('transform', 'similarity'))
        say(f'  fit: {len(cps)} points, residual max {max(res):.2f} m, {mpp * 100:.1f} cm/px, rotation {rot:.1f} deg'
            + (f', leave-one-out max {max(loo):.2f} m' if loo else ''))
    Minv = np.linalg.inv(M)
    to_ll = lambda u, v: [r6(t) for t in unproj(*apply(M, u, v))]
    from_xy = lambda X, Y: apply(Minv, X, Y)
    warp = None
    if icp and P.get('warp') == 'axes':
        fc0, _ = cv2.findContours(foot.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        sp = [cv2.approxPolyDP(c, 0.8 / mpp, True).reshape(-1, 2).astype(float).tolist() for c in fc0 if len(c) > 20]
        warp = AxisWarp(M, sp, shape, P.get('warp_tol_m', 4.0))
        to_ll = lambda u, v: [r6(t) for t in unproj(*warp.xy(u, v))]
        from_xy = warp.inv
        segs = [(r[i - 1], r[i]) for o, holes in shape for r in [o] + holes for i in range(len(r))]
        pts = np.concatenate([c[:, 0, :] for c in fc0 if len(c) > 20]).astype(float)[::5]
        _, dw = _seg_closest(np.array([warp.xy(u, v) for u, v in pts]), np.array([a for a, _ in segs], float), np.array([b for _, b in segs], float))
        say(f'  axis stretch: outline fit now median {np.median(dw):.2f} m, 90% within {np.quantile(dw, 0.9):.2f} m')
        icp.update({'similarity_median_m': icp['median_m'], 'similarity_p90_m': icp['p90_m'],
                    'median_m': round(float(np.median(dw)), 2), 'p90_m': round(float(np.quantile(dw, 0.9)), 2)})
    outline_px = [np.array([from_xy(*proj(*q)) for q in p['outer']], np.float64) for p in bdoc['outline']['polys']]

    # ink and text
    words = []
    boxmask = None
    if mode == 'vector':
        wall, words, nvec = pdf_walls(path, page, dpi, (H, W), P['min_lw_pt'])
        say(f'  vector: {nvec} PDF line objects drawn, {len(words)} words')
    else:
        rw = early_words if early_words is not None else (rapid_words_all(img, P['ocr_rotated'], P.get('ocr_upscale', 1.0)) if ctl.get('ocr', True) and P.get('engine', 'rapid') == 'rapid' else None)
        if rw is not None:
            words = rw
            say(f'  OCR (RapidOCR): {len(words)} words')
        elif ctl.get('ocr', True):
            if P['label_boxes']:
                words = box_labels(img)
            else:
                words = ocr_words(img, P['ocr_scale'], P['ocr_conf'])
                if P['ocr_rotated']:
                    words += [w for w in rotated_words(img, P['ocr_scale'], P['ocr_conf']) if not any(_iou(w['box'], o['box']) > 0.2 for o in words)]
            say(f'  OCR: {len(words)} words')
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        thr = P['dark'] if P['dark'] is not None else (70 if mode == 'fills' else otsu(gray))
        wall = gray < thr
        if P.get('wall_neutral'):
            # colored fills (Space Inventory's blue rooms) can be as dark as the gray walls: walls are the gray ones
            spread = img.max(axis=2).astype(np.int16) - img.min(axis=2)
            wall = (gray < P['wall_neutral']) & (spread < 30)
        if P['label_boxes']:
            # erase the whole white box each label sits in, frame and all
            m = np.zeros((H, W), np.uint8)
            wn, wlab, wst, _ = cv2.connectedComponentsWithStats((img > 225).all(axis=2).astype(np.uint8), connectivity=4)
            for w in words:
                b = [int(v) for v in w['box']]
                win = wlab[max(0, b[1]):b[3] + 1, max(0, b[0]):b[2] + 1]
                bw_, bh_ = b[2] - b[0] + 2, b[3] - b[1] + 2
                if P.get('label_box_max'):
                    # a label box is only a little bigger than its text; a room holding a label is not one
                    ids = [int(v) for v in np.unique(win) if v and wst[v, cv2.CC_STAT_WIDTH] <= P['label_box_max'] * bw_
                           and wst[v, cv2.CC_STAT_HEIGHT] <= P['label_box_max'] * bh_]
                else:
                    ids = [int(v) for v in np.unique(win) if v and wst[v, cv2.CC_STAT_AREA] < 60 * bw_ * bh_]
                for v in ids:
                    x_, y_, w_, h_ = wst[v, :4]
                    b = [min(b[0], x_), min(b[1], y_), max(b[2], x_ + w_), max(b[3], y_ + h_)]
                cv2.rectangle(m, (b[0] - 3, b[1] - 3), (b[2] + 3, b[3] + 3), 1, -1)
            wall &= ~m.astype(bool)
            boxmask = m.astype(bool)
        if P['hatch_m']:
            k = max(3, int(round(P['hatch_m'] / mpp)))
            ker = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
            closed = cv2.morphologyEx(wall.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
            hatch = cv2.morphologyEx(closed, cv2.MORPH_OPEN, ker)
            inner = cv2.erode(hatch, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))).astype(bool)
            hatch_mask = inner
            wall &= ~inner
    for lb in ctl.get('labels', []):
        x, y = lb['px']
        words.append({'text': lb['text'], 'conf': 100, 'box': [x - 1, y - 1, x + 1, y + 1], 'manual': True})
    wall &= roi
    if mode == 'walls' and not P['label_boxes']:
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
        free = ~sealed & roi & ~outside
        if boxmask is not None:
            free |= boxmask & roi
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

    if P.get('split', True):
        ns_ = split_by_labels(lab, regions, words, mpp, P['min_room_m2'], max(2, int(0.6 / mpp)), P.get('split_on', 'numbers'))
        if ns_:
            say(f'  {ns_} regions holding several room numbers split at their doorways')

    # labels -> regions
    unplaced = []
    near_px = max(2, int(1.0 / mpp))
    for w in words:
        cx, cy = (w['box'][0] + w['box'][2]) / 2, (w['box'][1] + w['box'][3]) / 2
        xi, yi = int(round(cx)), int(round(cy))
        if not (0 <= xi < W and 0 <= yi < H):
            continue
        i = w.get('_region') if w.get('_region') in regions else int(lab[yi, xi])
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
        kind = classify_text(' '.join(w['text'] for w in ws if not ROOMNUM.match(w['text'].upper())))
        for k_, rx in SUFFIX_KIND:
            if any(rx.search(t) for t in nums):
                kind = kind or k_
        if len(nums) == 1:
            r['ref'] = nums[0]
        elif len(nums) > 1:
            r['refs'] = nums
            r['flag'] = 'merged'
            flags.append(f'region at px {R["at"]} has {len(nums)} room numbers {nums}: rooms merged, add a cut')
        wcs = [f for f in bdoc['features'] if f['t'] == 'restroom' and f.get('room') and f.get('floor') == floor and
               any(re.sub(r'^0+', '', f['room'].upper()) == re.sub(r'^0+', '', t) for t in nums)]
        if wcs and kind in (None, 'room'):
            kind = 'restroom'
            r['name'] = wcs[0].get('name') or 'Restroom'
            if wcs[0].get('note'):
                r['name'] += ' (' + wcs[0]['note'] + ')'
        if not kind:
            if nums:
                kind = 'room'
            elif mode == 'fills' or len(re.sub(r'[^A-Za-z]', '', text)) >= 4:
                kind = 'room' if text else 'other'
            elif R['hw'] <= P['corridor_halfwidth_m'] and R['area'] >= 8 and R['area'] / (4 * R['hw'] ** 2) >= 3:
                kind = 'corridor'
            else:
                kind = 'other'
        wo = [w for w in ws if not ROOMNUM.match(w['text'].upper())]
        if any(len(w['text']) > 10 and ' ' not in w['text'] for w in wo):
            # RapidOCR runs words together ("StaffOffices"): read the same text again with tesseract for the spaces
            bx = [min(w['box'][0] for w in wo), min(w['box'][1] for w in wo), max(w['box'][2] for w in wo), max(w['box'][3] for w in wo)]
            t2 = spaced_text(img, bx, 3)
            if t2 and abs(len(t2.replace(' ', '')) - sum(len(w['text'].replace(' ', '')) for w in wo)) <= 3:
                wo = [{'text': t2, 'box': bx}]
        words_only = ' '.join(w['text'] for w in wo)
        if r.get('ref'):
            r['label'] = r['ref']
        elif r.get('refs'):
            r['label'] = '/'.join(r['refs'])
        elif words_only and kind != 'corridor':
            r['label'] = words_only[:28]
        if words_only and (r.get('ref') or r.get('refs')) and not r.get('name'):
            r['name'] = words_only[:40]
        r['kind'] = kind
        r.update(setters.get(i, {}))
        if setters.get(i, {}).get('ref') or setters.get(i, {}).get('label'):
            r.pop('flag', None)
            r.pop('refs', None)
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

    if ctl.get('rooms_from') == 'hand':
        rooms = []   # only the spaces listed under "add", read off the plan by hand

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

    # a newer plan of part of this floor wins: leave out what it already covers
    if ctl.get('skip_where_traced'):
        others = [r for r in FP.load_records(os.path.join(HERE, 'plans')).get(code, [])
                  if r['floor'] == floor and r['file'] != f'plans/{ctl.get("out") or FP.slug(code) + "-" + floor + ".json"}']
        pts = np.array([proj(*q) for r in others for x in r['rooms'] for q in (x.get('poly') or [x['at']])], np.float32)
        if len(pts):
            hull = cv2.convexHull(pts).reshape(-1, 2)
            before = len(rooms)
            rooms = [r for r in rooms if cv2.pointPolygonTest(hull.astype(np.float32), tuple(float(v) for v in proj(*r['at'])), True) < -1.0]
            say(f'  {before - len(rooms)} spaces left out: a newer plan of this floor covers them')

    # walls
    walls = []
    if mode != 'fills' and ctl.get('walls', True):
        for ln in sk_lines:
            if len(ln) < 2:
                continue
            c = np.array(ln, np.int32).reshape(-1, 1, 2)
            if cv2.arcLength(c, False) * mpp < P['min_wall_line_m']:
                continue
            ap = cv2.approxPolyDP(c, eps, False)
            wl = [to_ll(float(p[0][0]), float(p[0][1])) for p in ap]
            # line work well outside the building (page frame, scroll bars, neighbours) isn't a wall of this floor
            if min(dist_to_shape(proj(*q), shape) for q in wl) > P['outside_m']:
                continue
            walls.append(wl)
    wall_m = round(wpx * mpp, 2) if mode != 'fills' else None

    # check: features that carry a room number (UCLA's restroom records) against the traced room with that number
    checks = []
    for f in bdoc['features']:
        if f.get('room') and f.get('floor') == floor and f.get('acc') != 'building' and 'lat' in f and f.get('src') == 'ucla_gis':
            want = re.sub(r'^0+', '', f['room'].upper())
            hit = [r for r in rooms if r.get('ref') and re.sub(r'^0+', '', r['ref']) == want and r.get('poly')]
            g = proj(f['lat'], f['lon'])
            if hit:
                ring = [proj(*q) for q in hit[0]['poly']]
                d = dist_to_shape(g, [(ring, [])])
                checks.append({'room': f['room'], 'src': f['src'], 'off_m': round(d, 1)})
            else:
                checks.append({'room': f['room'], 'src': f['src'], 'off_m': None})
    if checks:
        ok = [c for c in checks if c['off_m'] is not None]
        say(f'  check: {len(ok)}/{len(checks)} numbered restrooms found on the plan; '
            f'{sum(1 for c in ok if c["off_m"] == 0)} of them inside their traced room, worst {max([c["off_m"] for c in ok], default=0)} m off')
        for c in checks:
            if c['off_m'] is None or c['off_m'] > 0:
                say(f'    {c["room"]} ({c["src"]}): ' + ('no traced room with that number' if c['off_m'] is None else f'{c["off_m"]} m outside its traced room'))

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
            'px_to_m': [[round(float(v), 6) for v in row] for row in M.tolist()],  # plan pixel -> projected metres (build_maps.proj), before any stretch
        },
        'trace': {'mode': mode, 'tool': 'maps/trace_plan.py', 'date': datetime.date.today().isoformat(),
                  'plan_file': os.path.basename(path), 'page': page, 'dpi': dpi,
                  'params': {k: v for k, v in ctl.get('params', {}).items()},
                  'sealed_doors': len(added), 'dropped': dropped, 'flags': flags,
                  'unplaced_labels': [w['text'] for w in unplaced]},
        'checks': checks,
        'wall_m': wall_m,
        'rooms': [{k: v for k, v in r.items() if not k.startswith('_')} for r in rooms],
        'walls': walls,
    }
    if icp:
        rec['fit'].update({'transform': 'similarity, plan footprint fitted to the building outline (ICP)' + (', then stretched along the building grid' if warp else ''),
                           'points': None, 'max_m': icp['p90_m'], 'rms_m': None, 'loo_max_m': None, **icp})
        if warp:
            rec['fit']['stretch'] = {'grid_deg': round(math.degrees(warp.th), 2),
                                     'x_pairs_m': [[round(a, 1), round(b, 1)] for a, b in warp.px],
                                     'y_pairs_m': [[round(a, 1), round(b, 1)] for a, b in warp.py]}
    return rec


PHOTO_DEFAULTS = {
    'photo_scale': 2.0,     # the rectified sign is drawn at this many pixels per photo pixel
    'dark': 170,            # ink, on the glare-flattened gray scale (paper = 230); thickened by a pixel
    'min_wall_m': 1.2,      # ink pieces smaller than this are text and icons
    'min_room_m2': 1.2,
    'door_m': 1.6,          # door gaps on signs are short; 0 = no door sealing
    'corridor_level': None, # region brightness above this is corridor (None: split the two clusters)
    'net_m': 6.0,           # ink pieces at least this long are walls whatever their shape
    'north_deg': 0.0,       # where north points on the sign, degrees clockwise from up (the sign's NORTH arrow)
}


def sign_footprint(norm, thr=170):
    """The building on a sign: closed boxes that touch each other (rooms), not the legend or label boxes."""
    import cv2
    from scipy import ndimage as ndi
    H, W = norm.shape
    d = (norm < thr).astype(np.uint8)
    mg = int(0.012 * max(H, W))
    d[:mg] = d[-mg:] = 0
    d[:, :mg] = d[:, -mg:] = 0
    d = cv2.dilate(d, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats((1 - d).astype(np.uint8), connectivity=4)
    border = set(np.unique(np.r_[lab[mg + 2], lab[-mg - 3], lab[:, mg + 2], lab[:, -mg - 3]]).tolist())
    keep = np.zeros(n, bool)
    amin = 0.0004 * H * W
    for i in range(1, n):
        if i in border or st[i, cv2.CC_STAT_AREA] < amin:
            continue
        keep[i] = True
    cand = keep[lab].astype(np.uint8)
    kk = int(0.04 * max(H, W)) | 1
    near = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kk, kk)))
    m, cl = cv2.connectedComponents(near, connectivity=8)
    sizes = ndi.sum(cand, cl, range(1, m))
    blob = cl == 1 + int(np.argmax(sizes))
    return ndi.binary_fill_holes(blob)


def region_poly(lab, i, sl, grow_k, eps, to_ll, H, W, pad, min_hole_px=1e9):
    import cv2
    ys, xs = slice(max(0, sl[0].start - pad), min(H, sl[0].stop + pad)), slice(max(0, sl[1].start - pad), min(W, sl[1].stop + pad))
    m = cv2.dilate((lab[ys, xs] == i).astype(np.uint8), grow_k)
    cs, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return None, None
    k_out = max((k for k in range(len(cs)) if hier[0][k][3] < 0), key=lambda k: cv2.contourArea(cs[k]))
    ring = lambda c: [to_ll(float(p[0][0] + xs.start), float(p[0][1] + ys.start)) for p in cv2.approxPolyDP(c, eps, True)]
    return ring(cs[k_out]), [ring(cs[k]) for k in range(len(cs)) if hier[0][k][3] == k_out and cv2.contourArea(cs[k]) > min_hole_px]


def trace_photo(ctl, ctl_path, debug=None):
    """A phone photo of a posted evacuation plan -> floor record."""
    import cv2
    from scipy import ndimage as ndi
    code, floor = ctl['building'], str(ctl['floor'])
    P = dict(DEFAULTS, **PHOTO_DEFAULTS, **ctl.get('params', {}))
    bfile = os.path.join(HERE, FP.slug(code) + '.json')
    with open(bfile, encoding='utf-8') as fh:
        bdoc = json.load(fh)
    shape = [([proj(*q) for q in p['outer']], [[proj(*q) for q in h] for h in p['holes']]) for p in bdoc['outline']['polys']]
    path = plan_path(ctl['plan'], ctl_path, code)
    raw = load_raster(path)
    img, corners = rectify(raw, ctl['plan'].get('corners'), P['photo_scale'])
    H, W = img.shape[:2]
    rdir = debug or os.path.join(HERE, '.cache', 'plans', FP.slug(code), 'review')
    os.makedirs(rdir, exist_ok=True)
    stem = os.path.join(rdir, f'{FP.slug(code)}-{floor}')
    cv2.imwrite(stem + '-rectified.png', cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    say(f'{code} floor {floor}: photo {os.path.basename(path)} {raw.shape[1]}x{raw.shape[0]}, sign frame {corners}, rectified {W}x{H} px')

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    norm = flatten(gray)
    dark = cv2.dilate((norm < P['dark']).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    rgb = img.astype(np.int16)
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    pink = (R - G > 35) & (R - B > 20)
    blue = (B - R > 25) & (B - G > 12)

    # the building: the enclosed spaces (rooms) that hang together, closed over the corridors and filled
    foot = sign_footprint(norm, P['dark'])
    cv2.imwrite(stem + '-footprint.png', (foot * 255).astype(np.uint8))
    cps = []
    if isinstance(ctl['control'], list):
        cps = resolve_control(ctl['control'], bdoc)
        M, res, loo, mpp, rot = georef(cps, ctl.get('transform', 'similarity'))
        med, p90 = float(np.median(res)), max(res)
        say(f'  fit: {len(cps)} control points, residual max {max(res):.2f} m' + (f', leave-one-out max {max(loo):.2f} m' if loo else ''))
    else:
        M, med, p90, _ = outline_icp(foot, shape, P['north_deg'], trim=P.get('icp_trim', 0.85))
        say(f'  fit: sign outline fitted to the building outline: median {med:.2f} m, 90% within {p90:.2f} m')
    mpp = math.sqrt(abs(np.linalg.det(M[:2, :2])))
    rot = math.degrees(math.atan2(M[1, 0], M[0, 0]))
    to_ll = lambda u, v: [r6(t) for t in unproj(*apply(M, u, v))]
    to_xy = lambda u, v: apply(M, u, v)
    from_xy = lambda X, Y: apply(np.linalg.inv(M), X, Y)
    say(f'  {mpp * 100:.1f} cm/px, rotation {rot:.1f} deg')
    warp = None
    if not cps and P.get('warp', 'axes') == 'axes':
        fc0, _ = cv2.findContours(foot.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        sp = [cv2.approxPolyDP(c, 0.8 / mpp, True).reshape(-1, 2).astype(float).tolist() for c in fc0 if len(c) > 20]
        warp = AxisWarp(M, sp, shape, P.get('warp_tol_m', 4.0))
        to_xy, from_xy = warp.xy, warp.inv
        to_ll = lambda u, v: [r6(t) for t in unproj(*warp.xy(u, v))]
        segs = [(r[i - 1], r[i]) for o, holes in shape for r in [o] + holes for i in range(len(r))]
        pts = np.concatenate([c[:, 0, :] for c in fc0 if len(c) > 20]).astype(float)[::5]
        Xw = np.array([warp.xy(u, v) for u, v in pts])
        _, dw = _seg_closest(Xw, np.array([a for a, _ in segs], float), np.array([b for _, b in segs], float))
        say(f'  axis stretch: {len(warp.px)} x and {len(warp.py)} y edge pairs; outline fit now median {np.median(dw):.2f} m, '
            f'90% within {np.quantile(dw, 0.9):.2f} m (was {med:.2f} / {p90:.2f})')
        icp_before = (med, p90)
        med, p90 = float(np.median(dw)), float(np.quantile(dw, 0.9))
    px = lambda m: max(1, int(round(m / mpp)))

    # solid ink: exit arrows, the YOU ARE HERE disc, stair treads, icons (much thicker than a wall line)
    thin = norm < P['dark']
    tw = wall_thickness_px(thin, skeleton(thin))
    closed = cv2.morphologyEx(thin.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (px(0.3), px(0.3))))
    ko = max(px(0.45), int(2.2 * tw) + 1)
    solid = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ko, ko)))
    ns, slab, sst, scen = cv2.connectedComponentsWithStats(solid, connectivity=8)
    stairs_m = np.zeros((H, W), bool)
    yah = None
    xb = []
    light = norm > P['dark']

    def diag_light(x0, y0, sw, sh):
        t = np.linspace(0.2, 0.8, 25)
        best = 0
        for dx in (-2, 0, 2):
            a = light[(y0 + t * sh).astype(int), (x0 + dx + t * sw).astype(int)].mean()
            b = light[(y0 + t * sh).astype(int), (x0 + dx + (1 - t) * sw).astype(int)].mean()
            best = max(best, min(a, b))
        return best
    # elevators: a dark box cut by a light X into four dark triangles
    nt, tlab, tst, tcen = cv2.connectedComponentsWithStats((thin & foot).astype(np.uint8), connectivity=4)
    tri = [i for i in range(1, nt) if 0.3 <= max(tst[i, 2], tst[i, 3]) * mpp <= 4 and 0.3 < tst[i, 4] / (tst[i, 2] * tst[i, 3]) < 0.7]
    tall = [j for j in tri if tst[j, 3] > 1.3 * tst[j, 2]]
    wide = [j for j in tri if tst[j, 2] > 1.3 * tst[j, 3]]
    for l_ in tall:
        for r_ in tall:
            hl = tst[l_, 3]
            dx = tcen[r_][0] - tcen[l_][0]
            if not (abs(tcen[l_][1] - tcen[r_][1]) < 0.15 * hl and 0.3 * hl < dx < 1.3 * hl):
                continue
            cx_, cy_ = (tcen[l_][0] + tcen[r_][0]) / 2, (tcen[l_][1] + tcen[r_][1]) / 2
            up = [u for u in wide if abs(tcen[u][0] - cx_) < 0.2 * hl and -0.6 * hl < tcen[u][1] - cy_ < 0]
            dn = [u for u in wide if abs(tcen[u][0] - cx_) < 0.2 * hl and 0 < tcen[u][1] - cy_ < 0.6 * hl]
            if up and dn and 0.8 <= hl * mpp <= 4 and not any(math.hypot(cx_ - o[0], cy_ - o[1]) < hl / 2 for o in xb):
                xb.append((cx_, cy_, hl / 2 + 3))
    for i in range(1, ns):
        x, y, w, h, a = sst[i]
        comp = slab[y:y + h, x:x + w] == i
        dens = thin[y:y + h, x:x + w][comp].mean()
        if os.environ.get('TRACE_DEBUG'):
            say(f'    solid {i}: {w}x{h} px at {x},{y}, {a * mpp * mpp:.1f} m2, density {dens:.2f}')
        am2 = a * mpp * mpp
        round_ = a / (math.pi * (max(w, h) / 2) ** 2)
        # elevator: a dark box with a light X (one car, or several side by side)
        k = max(1, int(round(max(w, h) / max(1, min(w, h)))))
        side = min(w, h)
        if False:
            pass
        if 0.7 < w / h < 1.4 and round_ > 0.7 and 0.8 < max(w, h) * mpp < 3.5 and foot[int(scen[i][1]), int(scen[i][0])]:
            if yah is None or a > yah[2]:
                yah = (scen[i][0], scen[i][1], a)
            continue
        sub = thin[y:y + h, x:x + w] & comp
        trans = max((np.diff(sub.astype(np.int8), axis=1) == 1).sum(1).mean(), (np.diff(sub.astype(np.int8), axis=0) == 1).sum(0).mean())
        if 0.3 < dens < P.get('stair_density', 0.85) and am2 >= 1.0 and trans >= 3.5 and foot[int(scen[i][1]), int(scen[i][0])]:
            stairs_m[y:y + h, x:x + w] |= comp
    allwords = (rapid_words(img) if ctl.get('ocr', True) else [])
    if allwords is None:
        allwords = ocr_words(img, 1, 50)
    say(f'  OCR: {len(allwords)} text pieces')
    textm = np.zeros((H, W), np.uint8)
    for w in allwords:
        b = [int(v) for v in w['box']]
        if (b[3] - b[1]) * mpp < 1.5:
            pd = 4 + int(0.35 * (b[3] - b[1]))   # OCR boxes can clip the first and last letter
            cv2.rectangle(textm, (b[0] - pd, b[1] - 4), (b[2] + pd, b[3] + 4), 1, -1)
    textm = textm.astype(bool)
    stairs_m &= ~cv2.dilate(textm.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    stairs_m = remove_small(stairs_m, 1.2 / mpp)
    cv2.imwrite(stem + "-stairs.png", (stairs_m * 255).astype(np.uint8))
    erase = cv2.dilate(solid, np.ones((5, 5), np.uint8)).astype(bool) | textm | cv2.dilate((pink | blue).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    wall = dark & ~erase & foot
    # drop text: pieces not part of the wall network that are too fat to be a wall line
    nn, wl, wst, _ = cv2.connectedComponentsWithStats(wall.astype(np.uint8), connectivity=8)
    keepw = np.zeros(nn, bool)
    for i in range(1, nn):
        w_, h_ = wst[i, cv2.CC_STAT_WIDTH], wst[i, cv2.CC_STAT_HEIGHT]
        keepw[i] = max(w_, h_) * mpp >= P['net_m'] or (max(w_, h_) * mpp >= P['min_wall_m'] and min(w_, h_) <= 2 * tw + 3)
    wall = keepw[wl]
    # the stair box keeps its outline
    wall |= (cv2.morphologyEx(stairs_m.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).astype(bool) & dark)
    # elevators: crossed boxes; their diagonals go, the box stays
    for cx, cy, hf in xb:
        x0, y0, x1, y1 = int(cx - hf + 3), int(cy - hf + 3), int(cx + hf - 3), int(cy + hf - 3)
        wall[y0:y1, x0:x1] = False
        cv2.rectangle(wall.view(np.uint8), (int(cx - hf), int(cy - hf)), (int(cx + hf), int(cy + hf)), 1, 2)
    for a, b in ctl.get('cuts', []):
        cv2.line(wall.view(np.uint8), tuple(int(v) for v in a), tuple(int(v) for v in b), 1, 3)
    for a, b in ctl.get('erase', []):
        cv2.line(wall.view(np.uint8), tuple(int(v) for v in a), tuple(int(v) for v in b), 0, 7)
    sk = skeleton(wall)
    wpx = wall_thickness_px(wall, sk)
    if P['door_m']:
        wall, sealed_ = seal_doors(wall, skel_graph(sk), sk, P['door_m'] / mpp, wpx)
        say(f'  {len(sealed_)} door gaps closed')
        sk = skeleton(wall)

    # rooms and corridors: walls split them, and so does the fill (rooms gray, corridors white)
    free = ~wall & foot
    # under the erased arrows and icons the fill is unknown: paint it in from around
    small = cv2.resize(norm, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    em = cv2.resize(erase.astype(np.uint8), (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    filled = cv2.resize(cv2.inpaint(small, cv2.dilate(em, np.ones((3, 3), np.uint8)), 3, cv2.INPAINT_TELEA), (W, H), interpolation=cv2.INTER_LINEAR)
    base = np.where(erase, filled, norm).astype(np.uint8)
    sm = cv2.medianBlur(base, 7)
    vals = sm[free & ~dark]
    tone_cut = P['corridor_level'] or float(cv2.threshold(vals.reshape(-1, 1).astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
    whiteish = sm > tone_cut
    n1, lab1 = cv2.connectedComponents((free & ~whiteish).astype(np.uint8), connectivity=4)
    n2, lab2 = cv2.connectedComponents((free & whiteish).astype(np.uint8), connectivity=4)
    lab = np.where(lab2 > 0, lab2 + n1, lab1).astype(np.int32)
    say(f'  fill tone cut {tone_cut:.0f} (rooms below, corridors above)')
    objs = ndi.find_objects(lab)
    regions = {}
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        m = lab[sl] == i
        area = m.sum() * mpp * mpp
        if area < P['min_room_m2']:
            continue
        dt = cv2.distanceTransform(np.pad(m, 1).astype(np.uint8), cv2.DIST_L2, 5)[1:-1, 1:-1]
        if dt.max() * mpp < P['min_halfwidth_m']:
            continue
        yy, xx = np.unravel_index(int(dt.argmax()), dt.shape)
        regions[i] = {'sl': sl, 'area': area, 'hw': dt.max() * mpp, 'at': (xx + sl[1].start, yy + sl[0].start),
                      'bright': float(np.median(norm[sl][m & ~dark[sl]])) if (m & ~dark[sl]).any() else 0}
    for pxy in ctl.get('drop', []):
        i = region_at(lab, pxy, regions)
        regions.pop(i, None)
    br = sorted(r['bright'] for r in regions.values())
    lvl = tone_cut
    blue_lab, nb = ndi.label(cv2.dilate(blue.astype(np.uint8), np.ones((3, 3), np.uint8)))
    blue_pts = [c for c in ndi.center_of_mass(blue, blue_lab, range(1, nb + 1)) if c] if nb else []
    blue_pts = [(x, y) for (y, x), sz in zip(blue_pts, ndi.sum(blue, blue_lab, range(1, nb + 1))) if sz * mpp * mpp > 0.15]

    # labels file: the human-checked truth once it exists
    lfile = os.path.join(HERE, 'plans', f'{FP.slug(code)}-{floor}.labels.json')
    human = []
    if os.path.exists(lfile):
        with open(lfile, encoding='utf-8') as fh:
            human = json.load(fh).get('regions', [])
    # only entries a person has checked override the tracer; the rest are refreshed every run
    human = [e for e in human if e.get('checked')]
    hset = {}
    for e in human:
        i = region_at(lab, e['px'], regions)
        if i is None:
            say(f'  labels file: nothing at px {e["px"]} ({e.get("text")})')
            continue
        hset[i] = e

    split_by_labels(lab, regions, allwords, mpp, P['min_room_m2'], px(0.6))
    inroom = {}
    for w in allwords:
        cx_, cy_ = int((w['box'][0] + w['box'][2]) / 2), int((w['box'][1] + w['box'][3]) / 2)
        i = w['_region'] if w.get('_region') in regions else int(lab[min(H - 1, cy_), min(W - 1, cx_)])
        if i not in regions:
            i = region_at(lab, (cx_, cy_), regions, r=px(0.6))
        if i in regions:
            inroom.setdefault(i, []).append(w)
    rooms, review = [], []
    grow = max(1, wpx // 2 + 1)
    gk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
    eps = max(0.8, P['simplify_m'] / mpp)
    for i, Rg in regions.items():
        sl = Rg['sl']
        mask_full = lab[sl] == i
        cx, cy = Rg['at']
        kind = 'corridor' if Rg['bright'] >= lvl else 'room'
        if (stairs_m[sl] & mask_full).sum() > P.get('stair_share', 0.35) * mask_full.sum() and Rg['area'] <= P.get('stair_max_m2', 45):
            kind = 'stair'
        if any(int(round(by)) in range(sl[0].start, sl[0].stop) and int(round(bx)) in range(sl[1].start, sl[1].stop) and lab[int(by), int(bx)] == i for bx, by, _ in xb):
            kind = 'elevator'
        if kind == 'room' and any(lab[int(by), int(bx)] == i for bx, by in blue_pts):
            kind = 'restroom'
        ocr = ''
        if kind in ('room', 'restroom') and ctl.get('ocr', True):
            ws_ = text_lines(inroom.get(i, []))
            ocr = ' '.join(w['text'] for w in ws_)
            if not ocr:
                ocr = region_text(norm[sl], mask_full)
        e = hset.get(i)
        text = e.get('text', '') if e else ocr
        if e and e.get('kind'):
            kind = e['kind']
        if e and e.get('drop'):
            continue
        r = {'kind': kind}
        toks = [t.upper() for t in text.split()]
        nums = []
        for t in toks:
            t2 = t.replace('O', '0') if re.fullmatch(r'[0-9O]{3,5}[A-Z]?', t) else t
            if re.fullmatch(r'[A-Z]{0,2}\d{3,5}[A-Z]{0,2}|[A-Z]{1,2}\d{2}', t2):
                nums.append(t2)
        # "2057 A" on two lines is room 2057A
        if len(nums) == 1 and len(toks) == 2 and re.fullmatch(r'[A-Z]', toks[1]) and toks[0] == nums[0]:
            nums = [nums[0] + toks[1]]
        words = [t for t in text.split() if t.upper() not in nums and not re.fullmatch(r'[A-Za-z]{0,2}\d{3,5}[A-Za-z]{0,2}', t)]
        if len(nums) == 1:
            r['ref'] = nums[0]
            r['label'] = nums[0]
        elif len(nums) > 1:
            r['label'] = '/'.join(nums)
            r['flag'] = 'merged'
        elif text and kind != 'corridor':
            r['label'] = text[:24]
        kw = classify_text(' '.join(words))
        if kw == 'restroom' and kind == 'room':
            r['kind'] = kind = 'restroom'
        if kind == 'restroom':
            w = ' '.join(words).lower()
            r['label'] = 'Women' if 'women' in w else 'Men' if re.search(r'\bmen\b', w) else r.get('label') or 'WC'
            r['name'] = f"{r['label'] if r['label'] in ('Women', 'Men') else 'Restroom'}{' ' + r['ref'] if r.get('ref') else ''}"
        poly, holes = region_poly(lab, i, sl, gk, eps, to_ll, H, W, grow + 2, 4.0 / mpp / mpp)
        if not poly or len(poly) < 3:
            continue
        r['poly'] = poly
        if holes:
            r['holes'] = holes
        r['_i'] = i
        r['_px'] = [int(cx), int(cy)]
        rooms.append(r)
        review.append({'px': [int(cx), int(cy)], 'kind': r['kind'], 'text': text, 'ocr': ocr, 'checked': False})

    for cx, cy, hf in xb:
        sq = [(cx - hf, cy - hf), (cx + hf, cy - hf), (cx + hf, cy + hf), (cx - hf, cy + hf)]
        rooms = [r for r in rooms if not (r.get('_i') and lab[int(cy), int(cx)] == r['_i'] and regions[r['_i']]['area'] < 4 * (hf * mpp) ** 2 * 2)]
        rooms.append({'kind': 'elevator', 'label': '', 'name': 'Elevator', 'poly': [to_ll(*q) for q in sq], '_px': [int(cx), int(cy)]})
    say(f'  elevator cars: {[(int(x), int(y)) for x, y, _ in xb]}')

    # sign text: stair names, connections to other buildings
    used = {id(w) for ws_ in inroom.values() for w in ws_}
    blocks = text_blocks([w for w in allwords if id(w) not in used], gap=1.0)
    stairs = [r for r in rooms if r['kind'] == 'stair']
    fc, _ = cv2.findContours(foot.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    fpts = np.concatenate([c[:, 0, :] for c in fc]).astype(float)
    extra = []
    for b in blocks:
        t = re.sub(r'\s+', ' ', b['text']).strip()
        if re.match(r'(T0|TO)[A-Z]', t) or re.search(r'NOROOF|ROOFACCESS', t) or any(len(x) > 10 for x in t.split()):
            t = spaced_text(img, b['box']) or t
        bc = np.array([(b['box'][0] + b['box'][2]) / 2, (b['box'][1] + b['box'][3]) / 2])
        mS = re.search(r'STAIR\s*(\w+)?', t, re.I)
        if mS and stairs:
            near = min(stairs, key=lambda r: math.dist(r['_px'], bc))
            if math.dist(near['_px'], bc) * mpp < 15 and not near.get('label'):
                near['label'] = f'Stair {mS.group(1)}' if mS.group(1) and mS.group(1).upper() != 'UP' else 'Stair'
                rest = t[mS.end():].strip()
                near['name'] = (near['label'] + (f' ({rest.lower()})' if rest else '')).replace('(no roof access)', '(no roof access)')
        mT = re.match(r'(?:T0|TO)\s+([A-Z][^|]+?)[\s\W]*$', t, re.I)
        if mT:
            k = int(np.argmin(np.linalg.norm(fpts - bc, axis=1)))
            if np.linalg.norm(fpts[k] - bc) * mpp < 15:
                dest = re.sub(r'\s*&\s*', ' and ', mT.group(1))
                dest = ' '.join(t for t in dest.split() if len(t) > 2 or t.lower() in ('and', 'of'))
                dest = re.sub(r'\bSci\b', 'Sciences', dest.title()).replace(' And ', ' and ').replace(' Of ', ' of ')
                extra.append({'kind': 'exit', 'label': 'To ' + dest, 'name': 'Connection to ' + dest,
                              'at': to_ll(*fpts[k]), '_px': [int(fpts[k][0]), int(fpts[k][1])]})
    for e in ctl.get('add', []):
        r = {k: v for k, v in e.items() if k not in ('px', 'pt')}
        if 'px' in e:
            r['poly'] = [to_ll(*q) for q in e['px']]
        if 'pt' in e:
            r['at'] = to_ll(*e['pt'])
        r['hand'] = True
        extra.append(r)
    for a in ctl.get('set', []):
        i = region_at(lab, a['px'], regions)
        for r in rooms:
            if r.get('_i') == i:
                r.update({k: v for k, v in a.items() if k != 'px'})
                if a.get('ref') or a.get('label'):
                    r.pop('flag', None)
                    r.pop('refs', None)
    rooms += extra

    # write the review file for the human pass (keeps what's already in it)
    if ctl.get('_dry'):
        lfile = stem + '-labels.json'
    known = {tuple(e['px']) for e in human}
    out = human + [e for e in review if tuple(e['px']) not in known and not any(region_at(lab, h['px'], regions) == region_at(lab, e['px'], regions) for h in human)]
    with open(lfile, 'w', encoding='utf-8') as fh:
        json.dump({'about': ('One entry per traced space, by a pixel inside it on the rectified sign '
                             f'(maps/.cache/plans/{FP.slug(code)}/review/{FP.slug(code)}-{floor}-rectified.png). Fix "text" '
                             '(the room number or name as printed) and "kind" (room, corridor, stair, elevator, restroom, mech, '
                             'other), or add "drop": true for a space that is not real, and set "checked": true on every entry you '
                             'looked at; then rerun the control file. Entries with "checked": true override the tracer; the others '
                             'are rewritten on every run. "ocr" is what OCR read.'),
                   'regions': out}, fh, indent=1, ensure_ascii=False)

    walls = []
    for ln in skel_graph(sk):
        c = np.array(ln, np.int32).reshape(-1, 1, 2)
        if len(ln) > 1 and cv2.arcLength(c, False) * mpp >= P['min_wall_line_m']:
            walls.append([to_ll(float(q[0][0]), float(q[0][1])) for q in cv2.approxPolyDP(c, eps, False)])

    checks = []
    for f in bdoc['features']:
        if f.get('room') and f.get('floor') == floor and 'lat' in f and f.get('acc') != 'building' and f.get('src') == 'ucla_gis':
            want = re.sub(r'^0+', '', f['room'].upper())
            hit = [r for r in rooms if r.get('ref') and re.sub(r'^0+', '', r['ref']) == want and r.get('poly')]
            d = None
            if hit:
                d = round(dist_to_shape(proj(f['lat'], f['lon']), [([proj(*q) for q in hit[0]['poly']], [])]), 1)
            checks.append({'room': f['room'], 'src': f['src'], 'off_m': d})
    kinds = {}
    for r in rooms:
        kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
    say(f'  {len(rooms)} spaces {kinds}; corridor brightness cut {lvl}; {len(xb)} crossed boxes, {len(blue_pts)} restroom icons'
        + (', YOU ARE HERE found' if yah else ''))
    say(f'  room numbers: {sorted(r["ref"] for r in rooms if r.get("ref"))}')
    for c in checks:
        say(f'  check {c["room"]} ({c["src"]}): ' + ('not on the traced plan' if c['off_m'] is None else f'{c["off_m"]} m from its traced room'))
    write_debug(stem, img, lab, rooms, {r['_i']: {'at': r['_px']} for r in rooms if r.get('_i')}, wall, sk, [], [], M, [], [], [])
    ov = cv2.imread(stem + '-trace.png')
    for r in rooms:
        if r.get('_px') and not r.get('_i'):
            cv2.drawMarker(ov, tuple(r['_px']), (0, 160, 0), cv2.MARKER_TRIANGLE_UP, 30, 3)
            cv2.putText(ov, r['label'], (r['_px'][0] + 10, r['_px'][1]), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 120, 0), 2)
    fpx = [np.array([from_xy(*proj(*q)) for q in p['outer']]) for p in bdoc['outline']['polys']]
    for q in fpx:
        cv2.polylines(ov, [np.round(q).astype(np.int32)], True, (0, 140, 255), 3)
    cv2.imwrite(stem + '-trace.png', ov)

    date = ctl['source'].get('date', '')
    rec = {
        'format': FP.FORMAT, 'building': code, 'floor': floor,
        'title': ctl.get('title') or f'{bdoc["name"]}, floor {floor}',
        'extent': ctl.get('extent', 'full'),
        'source': ctl['source'],
        'how': ctl.get('how') or ('Traced from a photo of the evacuation plan posted on this floor. The photo was squared up '
                                  'from the sign\'s frame; walls are its dark lines, corridors its white spaces, rooms its gray '
                                  'boxes; stairs are the hatched boxes, elevators the crossed boxes, restrooms the boxes with '
                                  'the restroom icon. Room numbers were read by OCR and checked by hand. The sign has no scale, '
                                  'so its building outline was fitted to the mapped outline (with north as its NORTH arrow shows).'),
        'notes': ctl.get('notes', []),
        'fit': {'transform': 'similarity, sign outline fitted to the building outline (ICP)' + (', then stretched along the building grid to match its walls' if warp else ''),
                'similarity_median_m': round(icp_before[0], 2) if warp else None, 'similarity_p90_m': round(icp_before[1], 2) if warp else None,
                'points': None,
                'max_m': round(p90, 2), 'rms_m': None, 'median_m': round(med, 2), 'p90_m': round(p90, 2), 'loo_max_m': None,
                'm_per_px': round(mpp, 4), 'rot_deg': round(rot, 2), 'control': []},
        'trace': {'mode': 'photo', 'tool': 'maps/trace_plan.py', 'date': datetime.date.today().isoformat(),
                  'plan_file': os.path.basename(path), 'sign_corners_px': corners, 'rectified_px': [W, H],
                  'params': ctl.get('params', {}), 'labels_file': os.path.basename(lfile)},
        'checks': checks,
        'wall_m': round(wpx * mpp, 2),
        'rooms': [{k: v for k, v in r.items() if not k.startswith('_')} for r in rooms],
        'walls': walls,
    }
    if yah:
        rec['sign_at'] = to_ll(yah[0], yah[1])
    if warp:
        rec['fit']['stretch'] = {'grid_deg': round(math.degrees(warp.th), 2),
                                 'x_pairs_m': [[round(a, 1), round(b, 1)] for a, b in warp.px],
                                 'y_pairs_m': [[round(a, 1), round(b, 1)] for a, b in warp.py]}
    return rec


def default_how(mode, ctl, cps):
    what = {'walls': 'Walls are the dark lines of the plan image; rooms are the spaces they enclose, with door gaps closed by extending each wall end up to 2 m.',
            'vector': "Walls are the plan's own vector lines (from the PDF), rooms are the spaces they enclose, and room numbers are the PDF's text.",
            'fills': 'Rooms are the solid colored blocks of the map; labels were read by OCR and checked by hand.'}[mode]
    if not cps:
        return (f'{what} The plan has no scale or north arrow, so its building outline was fitted to the mapped outline '
                f'(scale, rotation and shift, by iterative closest point).')
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
    rng = np.random.default_rng(7)
    for r in rooms:
        i = r.get('_i')
        if i:
            # each space its own shade of its kind's color, so merged spaces stand out
            col[lab == i] = np.clip(np.array(KIND_BGR[r['kind']]) + rng.integers(-60, 60, 3), 0, 255)
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

# ---------- regularize: snap rooms and walls to the plan's grid ----------
# Traced polygons come off a raster: door swings and window dashes bite into rooms, thin walls wobble a
# few degrees, and simplification leaves kinks. Buildings are rectilinear almost everywhere, so each
# rectilinear room is rebuilt from its wall lines snapped to the plan's grid (or the room's own grid,
# for an angled wing), short jogs (door bites) are dropped, and wall lines are snapped the same way.
# Rooms that aren't rectilinear (curved, octagonal, diagonal walls) are left as traced.

def _grid_angle(segs):
    """Dominant axis of a set of segments (metres), period 90 degrees, in radians within (-45, 45]."""
    s = c = 0.0
    for (x0, y0), (x1, y1) in segs:
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 0.3:
            continue
        a = 4 * math.atan2(y1 - y0, x1 - x0)
        s += L * math.sin(a)
        c += L * math.cos(a)
    return math.atan2(s, c) / 4 if (s or c) else 0.0


def _wrap90(deg):
    return (deg + 45) % 90 - 45


def _rot(pts, ang):
    ca, sa = math.cos(ang), math.sin(ang)
    return [(x * ca - y * sa, x * sa + y * ca) for x, y in pts]


def _ring_segs(pts):
    return [(pts[i - 1], pts[i]) for i in range(len(pts))]


def _area(pts):
    return 0.5 * sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))


ORTHO_MIN_AXIS = 0.6    # share of a ring's edge length that must lie along its grid axes for it to count as rectilinear
ORTHO_JOG_M = 0.9       # jogs shorter than this (door bites, pixel steps) are dropped


def _orthogonalize(pts, tol_deg=12.0, jog_m=None):
    jog_m = ORTHO_JOG_M if jog_m is None else jog_m
    """Rebuild a ring (grid already axis-aligned) from axis-snapped runs of its edges; None when it isn't rectilinear."""
    n = len(pts)
    if n < 4:
        return None
    runs, tot, ok = [], 0.0, 0.0
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        ang = abs(math.degrees(math.atan2(dy, dx))) % 180
        tot += L
        if min(ang, 180 - ang, abs(ang - 90)) <= tol_deg:
            ok += L
        k = 'H' if abs(dx) >= abs(dy) else 'V'
        coord = (y0 + y1) / 2 if k == 'H' else (x0 + x1) / 2
        if runs and runs[-1][0] == k:
            runs[-1][1] += coord * L
            runs[-1][2] += L
        else:
            runs.append([k, coord * L, L])
    if tot == 0 or ok / tot < ORTHO_MIN_AXIS:
        return None
    if len(runs) > 1 and runs[0][0] == runs[-1][0]:
        runs[0][1] += runs[-1][1]
        runs[0][2] += runs[-1][2]
        runs.pop()
    # drop short jogs (door bites, pixel steps): a run whose neighbours sit within jog_m of each other
    while len(runs) > 4:
        m = len(runs)
        pos = [r[1] / r[2] for r in runs]
        j = min(range(m), key=lambda j: abs(pos[(j + 1) % m] - pos[j - 1]))
        if abs(pos[(j + 1) % m] - pos[j - 1]) >= jog_m:
            break
        a, b = runs[j - 1], runs[(j + 1) % m]
        a[1] += b[1]
        a[2] += b[2]
        for idx in sorted({j, (j + 1) % m}, reverse=True):
            runs.pop(idx)
    if len(runs) < 4 or len(runs) % 2:
        return None
    pos = [r[1] / r[2] for r in runs]
    out = []
    for j in range(len(runs)):
        if runs[j - 1][0] == 'H':
            out.append((pos[j], pos[j - 1]))
        else:
            out.append((pos[j - 1], pos[j]))
    return out


def _snap_ring_ll(ring, grid, plan_grid):
    """ring: [[lat, lon], ...]. Returns the regularized ring or None."""
    pts = [proj(a, b) for a, b in ring]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 4:
        return None
    own = _grid_angle(_ring_segs(pts))
    ang = plan_grid if abs(_wrap90(math.degrees(own - plan_grid))) <= 7 else own
    q = _rot(pts, -ang)
    new = _orthogonalize(q)
    if new is None:
        return None
    a0, a1 = abs(_area(q)), abs(_area(new))
    if a0 < 0.5 or abs(a1 - a0) / a0 > 0.3:
        return None
    # the new ring must stay close to the traced one
    def d_pt_seg(p, a, b):
        ax, ay = a; bx, by = b; px, py = p
        dx, dy = bx - ax, by - ay
        t = 0 if dx == dy == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - ax - t * dx, py - ay - t * dy)
    segs = _ring_segs(new)
    if max(min(d_pt_seg(p, a, b) for a, b in segs) for p in q) > 1.5:
        return None
    try:
        from shapely.geometry import Polygon
        if not Polygon(new).is_valid:
            return None
    except Exception:
        pass
    return [[r6(v) for v in unproj(*xy)] for xy in _rot(new, ang)]


def _snap_wall_ll(line, plan_grid, tol_deg=12.0, short_diag_m=1.5):
    """Returns a list of polylines: the line snapped to the grid, split where short diagonal bits were cut out."""
    pts = [proj(a, b) for a, b in line]
    if len(pts) < 2:
        return [line]
    q = _rot(pts, -plan_grid)
    cls = []
    for i in range(len(q) - 1):
        dx, dy = q[i + 1][0] - q[i][0], q[i + 1][1] - q[i][1]
        ang = abs(math.degrees(math.atan2(dy, dx))) % 180
        d = min(ang, 180 - ang, abs(ang - 90))
        cls.append(None if d > tol_deg else ('H' if abs(dx) >= abs(dy) else 'V'))
    q = [list(p) for p in q]
    i = 0
    while i < len(cls):
        k = cls[i]
        if k is None:
            i += 1
            continue
        j = i
        while j + 1 < len(cls) and cls[j + 1] == k:
            j += 1
        idx = list(range(i, j + 2))
        c = 0 if k == 'V' else 1
        w = [math.hypot(q[t + 1][0] - q[t][0], q[t + 1][1] - q[t][1]) for t in range(i, j + 1)]
        vals = [(q[t][c] + q[t + 1][c]) / 2 for t in range(i, j + 1)]
        pos = sum(v * ww for v, ww in zip(vals, w)) / max(sum(w), 1e-9)
        for t in idx:
            q[t][c] = pos
        i = j + 1
    # short diagonal bits are door swings and furniture off the raster, not walls: cut them out
    pieces, cur = [], [q[0]]
    for i in range(len(q) - 1):
        L = math.hypot(q[i + 1][0] - q[i][0], q[i + 1][1] - q[i][1])
        if cls[i] is None and L < short_diag_m:
            if len(cur) >= 2:
                pieces.append(cur)
            cur = [q[i + 1]]
        else:
            cur.append(q[i + 1])
    if len(cur) >= 2:
        pieces.append(cur)
    return [[[r6(v) for v in unproj(*xy)] for xy in _rot([tuple(p) for p in piece], plan_grid)] for piece in pieces]


def regularize_record(rec):
    """Snap the record's room polygons and wall lines to the plan grid, in place. Returns (rooms changed, rooms kept)."""
    rooms = [r for r in rec.get('rooms', []) if r.get('poly')]
    segs = []
    for r in rooms:
        segs += _ring_segs([proj(a, b) for a, b in r['poly']])
    for w in rec.get('walls', []):
        p = [proj(a, b) for a, b in w]
        segs += list(zip(p, p[1:]))
    if not segs:
        return 0, 0
    grid = _grid_angle(segs)
    changed = kept = 0
    for r in rooms:
        if r.get('kind') in ('pod', 'xbox'):
            kept += 1
            continue
        new = _snap_ring_ll(r['poly'], grid, grid)
        if new is None:
            kept += 1
            continue
        r['poly'] = new
        if r.get('holes'):
            r['holes'] = [(_snap_ring_ll(h, grid, grid) or h) for h in r['holes']]
        changed += 1
    rec['walls'] = [piece for w in rec.get('walls', []) for piece in _snap_wall_ll(w, grid)]
    rec.setdefault('trace', {})['regularized'] = {'grid_deg': round(math.degrees(grid), 2), 'rooms_snapped': changed, 'rooms_as_traced': kept}
    return changed, kept


def regularize_all():
    pdir = os.path.join(HERE, 'plans')
    for fn in sorted(os.listdir(pdir)):
        if not fn.endswith('.json') or fn.endswith('.control.json') or fn.endswith('.labels.json'):
            continue
        path = os.path.join(pdir, fn)
        with open(path, encoding='utf-8') as fh:
            rec = json.load(fh)
        if rec.get('format') is None or 'rooms' not in rec:
            continue
        ch, kp = regularize_record(rec)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(rec, fh, ensure_ascii=False, separators=(',', ':'))
        say(f'  {fn}: {ch} rooms snapped to the grid, {kp} kept as traced')


def write_record(rec, name=None):
    pdir = os.path.join(HERE, 'plans')
    regularize_record(rec)
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


def photo_control(photo, a):
    """Start (or update) the control file for a photo of a posted evacuation plan."""
    import shutil
    if not (a.building and a.floor):
        raise SystemExit('a photo needs --building and --floor')
    code, floor = a.building.upper(), str(a.floor)
    if not os.path.exists(os.path.join(HERE, FP.slug(code) + '.json')):
        raise SystemExit(f'no maps/{FP.slug(code)}.json; use a code from maps/index.json')
    ext = os.path.splitext(photo)[1].lower() or '.jpg'
    name = f'{FP.slug(code)}-{floor}-evac{ext}'
    dst = os.path.join(HERE, '.cache', 'plans', FP.slug(code), name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.abspath(photo) != os.path.abspath(dst):
        shutil.copyfile(photo, dst)
    date = a.date or datetime.date.fromtimestamp(os.path.getmtime(photo)).isoformat()
    cp = os.path.join(HERE, 'plans', f'{FP.slug(code)}-{floor}.control.json')
    ctl = {}
    if os.path.exists(cp):
        with open(cp, encoding='utf-8') as fh:
            ctl = json.load(fh)
    ctl.update({'building': code, 'floor': floor, 'mode': 'photo', 'extent': ctl.get('extent', 'full')})
    ctl['plan'] = dict(ctl.get('plan', {}), file=name)
    if a.corners:
        v = [float(t) for t in a.corners.split(',')]
        ctl['plan']['corners'] = [v[i:i + 2] for i in range(0, 8, 2)]
    ctl.setdefault('source', {})
    ctl['source'].update({'publisher': 'UCLA', 'what': f'posted evacuation plan, floor {floor}, photographed {date}',
                          'date': f'photographed {date}', 'kind': 'posted evacuation plan (photo)'})
    ctl.setdefault('control', {'auto': 'outline'})
    with open(cp, 'w', encoding='utf-8') as fh:
        json.dump(ctl, fh, indent=1, ensure_ascii=False)
    say(f'control file maps/plans/{os.path.basename(cp)} (edit it, or maps/plans/{FP.slug(code)}-{floor}.labels.json, and rerun it)')
    return cp


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('control', nargs='*', help='control file(s), or a photo of a posted evacuation plan (with --building and --floor)')
    ap.add_argument('--building', help='photo: building code, as in maps/index.json (e.g. WGYOUNG)')
    ap.add_argument('--floor', help='photo: floor (e.g. 2)')
    ap.add_argument('--date', help='photo: the day it was taken (YYYY-MM-DD); default: the file date')
    ap.add_argument('--corners', help='photo: the sign\'s corners TL,TR,BR,BL as x,y,x,y,... in photo pixels, if the frame isn\'t found')
    ap.add_argument('--debug', help='write overlay PNGs here')
    ap.add_argument('--dry-run', action='store_true', help="don't write anything but the debug images")
    ap.add_argument('--render', help='rasterize this plan with a pixel grid (for picking control points)')
    ap.add_argument('--page', type=int, default=1)
    ap.add_argument('--dpi', type=float, default=150)
    ap.add_argument('--out', help='output PNG for --render')
    ap.add_argument('--merge-all', action='store_true', help='re-merge every floor record into maps/<CODE>.json')
    ap.add_argument('--regularize-all', action='store_true', help='snap every floor record\'s rooms and walls to its plan grid, then merge')
    a = ap.parse_args()
    if a.render:
        render_grid(a.render, a.page, a.dpi, a.out or 'grid.png')
        return
    touched = set()
    controls = []
    for cp in a.control:
        if cp.lower().endswith('.json'):
            controls.append(cp)
        else:
            controls.append(photo_control(cp, a))
    for cp in controls:
        with open(cp, encoding='utf-8') as fh:
            ctl = json.load(fh)
        ctl['_dry'] = a.dry_run
        rec = trace_photo(ctl, cp, a.debug) if ctl.get('mode') == 'photo' else trace(ctl, cp, a.debug)
        if not a.dry_run:
            write_record(rec, ctl.get('out'))
            touched.add(rec['building'])
    if a.regularize_all:
        regularize_all()
    if (touched or a.merge_all or a.regularize_all) and not a.dry_run:
        merge_all(touched)


if __name__ == '__main__':
    main()
