"""Floor records: rooms and walls traced from published floor plans.

A floor record is a JSON file in maps/plans/ written by trace_plan.py
(`"format": "floorplan/1"`). It is already georeferenced (lat/lon). Both
build_maps.py and trace_plan.py use this module to merge the records into
maps/<CODE>.json, so a full rebuild keeps the traced floors.

In maps/<CODE>.json each traced floor looks like:

    "floors": {
      "1": {
        "plans": [{file, title, extent, source: {publisher, what, url, page, date, accessed},
                   how, fit: {transform, points, max_m, rms_m, m_per_px, rot_deg, control: [...]}, notes}],
        "rooms": [{ref?, refs?, kind, label?, name?, poly: [[lat, lon], ...], holes?, at?, p, flag?}
                  or {ref?, kind: "pod", label?, at: [lat, lon], p}],
        "walls": [[[lat, lon], ...], ...],      # wall centre lines
        "wall_m": 0.25                          # typical wall thickness on the plan, metres (or null)
      }
    }

kind is one of room, corridor, stair, elevator, restroom, exit, mech, other,
xbox (a crossed box with no legend), pod (a point, like a table). `p` is the
index of the plan in `plans` that the room came from. `at` is where the label
goes (a point well inside the polygon). Nothing in a floor record is guessed:
every room is a region drawn on the published plan.

Standard library only (build_maps.py must not need OpenCV).
"""
import json
import os
import re

FORMAT = 'floorplan/1'
KINDS = ('room', 'corridor', 'stair', 'elevator', 'restroom', 'exit', 'mech', 'other', 'xbox', 'pod')
META = ('title', 'extent', 'source', 'how', 'fit', 'notes', 'trace')
LEVEL_ORDER = {'C': -3, 'B': -2, 'A': -1, 'CS': -0.5, '0': 0, 'M': 1.5, 'P': 0.5, 'G': 0.25, 'L': 0.1}


def level_key(lv):
    if lv in LEVEL_ORDER:
        return (LEVEL_ORDER[lv], lv)
    return (int(lv), lv) if lv.isdigit() else (50, lv)


def is_record(doc):
    return isinstance(doc, dict) and doc.get('format') == FORMAT


def load_records(plans_dir):
    """{code: [record, ...]} for every floor record in maps/plans/."""
    out = {}
    if not os.path.isdir(plans_dir):
        return out
    for fn in sorted(os.listdir(plans_dir)):
        if not fn.endswith('.json') or fn.endswith('.control.json'):
            continue
        with open(os.path.join(plans_dir, fn), encoding='utf-8') as fh:
            doc = json.load(fh)
        if is_record(doc):
            doc['file'] = 'plans/' + fn
            out.setdefault(doc['building'], []).append(doc)
    return out


def _centroid(poly):
    a = cx = cy = 0.0
    for i in range(len(poly)):
        (y1, x1), (y2, x2) = poly[i - 1], poly[i]
        f = x1 * y2 - x2 * y1
        a += f
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    if abs(a) < 1e-15:
        return poly[0]
    return [round(cy / (3 * a), 6), round(cx / (3 * a), 6)]


def room_point(r):
    return r.get('at') or _centroid(r['poly'])


def merge_into(doc, records):
    """Put the floor records for one building into its map document (in place)."""
    floors = {}
    for rec in sorted(records, key=lambda r: r['file']):
        f = floors.setdefault(rec['floor'], {'plans': [], 'rooms': [], 'walls': [], 'wall_m': None})
        i = len(f['plans'])
        meta = {k: rec[k] for k in META if k in rec}
        meta['file'] = rec['file']
        f['plans'].append(meta)
        for r in rec['rooms']:
            f['rooms'].append(dict(r, p=i))
        f['walls'] += rec.get('walls', [])
        if rec.get('wall_m'):
            f['wall_m'] = rec['wall_m'] if f['wall_m'] is None else min(f['wall_m'], rec['wall_m'])
    doc.pop('plans', None)
    if not floors:
        doc.pop('floors', None)
        doc.pop('rooms', None)
        return doc
    doc['floors'] = {k: floors[k] for k in sorted(floors, key=level_key)}
    lookup = {}
    for lv, f in doc['floors'].items():
        for r in f['rooms']:
            for ref in ([r['ref']] if r.get('ref') else []):
                pt = room_point(r)
                lookup[ref] = {'floor': lv, 'lat': pt[0], 'lon': pt[1], 'plan': f['plans'][r['p']]['file']}
    doc['rooms'] = lookup
    doc['levels'] = sorted(set(doc.get('levels') or []) | set(floors), key=level_key)
    return doc


def coverage_text(records):
    """One line per traced floor, for the coverage table in SOURCES.md."""
    out = []
    for rec in sorted(records, key=lambda r: level_key(r['floor'])):
        n = sum(1 for r in rec['rooms'] if r['kind'] != 'pod')
        fit = rec.get('fit') or {}
        out.append(f"floor {rec['floor']}: {n} spaces from {rec['source'].get('publisher', '?')} "
                   f"({rec['source'].get('date', 'undated')}), fit max {fit.get('max_m', '?')} m")
    return out


def write_plans_md(path, records_by_code, today):
    """Rewrite the traced-plan table between the plans markers in maps/SOURCES.md."""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    a, b = '<!-- plans:start -->', '<!-- plans:end -->'
    if a not in text or b not in text:
        return
    rows = []
    for code in sorted(records_by_code):
        for rec in sorted(records_by_code[code], key=lambda r: level_key(r['floor'])):
            s, fit, tr = rec['source'], rec.get('fit') or {}, rec.get('trace') or {}
            url = s.get('url') or s.get('page') or ''
            src = f"[{s.get('publisher', '?')}: {s.get('what', 'plan')}]({url})" if url else s.get('what', '?')
            kinds = {}
            for r in rec['rooms']:
                kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
            ctl = '; '.join(f"{c['what']} {c['res_m']} m" for c in fit.get('control', []))
            if fit.get('median_m') is not None:
                ctl = f"outline fit: median {fit['median_m']} m, 90% within {fit['p90_m']} m"
                if fit.get('similarity_median_m') is not None:
                    ctl += f" (before the stretch: {fit['similarity_median_m']} / {fit['similarity_p90_m']} m)"
            chk = [c for c in rec.get('checks', []) if c.get('off_m') is not None]
            if rec.get('checks'):
                ctl += (f"; UCLA restroom check: {sum(1 for c in chk if c['off_m'] == 0)} of {len(rec['checks'])} "
                        f"inside the traced room with their number")
            flags = len([r for r in rec['rooms'] if r.get('flag')])
            npts = f", {fit['points']} points" if fit.get('points') else ''
            rows.append(
                f"| {code} | {rec['floor']} | {rec.get('extent', 'full')} | {src} | {s.get('date', 'undated')} | "
                f"{tr.get('mode', '?')} | {fit.get('transform', '?')}{npts}: {ctl} "
                f"({'max ' + str(fit['max_m']) + ' m, ' if fit.get('max_m') is not None and fit.get('median_m') is None else ''}"
                f"{'rms ' + str(fit['rms_m']) + ' m, ' if fit.get('rms_m') is not None else ''}{fit.get('m_per_px', '?')} m/px) | "
                f"{', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}; {len(rec.get('walls', []))} wall lines"
                f"{f'; {flags} flagged' if flags else ''} | `{rec['file']}` |")
    head = ('| Code | Floor | Extent | Plan | Plan date | Trace | Georeference (control point residuals) | Contents | File |\n'
            '|---|---|---|---|---|---|---|---|---|')
    body = f'{a}\n_Generated by trace_plan.py on {today}._\n\n' + head + '\n' + '\n'.join(rows) + f'\n{b}'
    if not rows:
        body = f'{a}\n_No traced plans yet._\n{b}'
    text = text[:text.index(a)] + body + text[text.index(b) + len(b):]
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def slug(code):
    return re.sub(r'[^A-Za-z0-9]+', '_', code).strip('_')
