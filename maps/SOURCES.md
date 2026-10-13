# Map data sources

Everything in `maps/*.json` comes from the sources below, put together by `build_maps.py`. Nothing is drawn from memory or guessed. If no source places a stairwell, restroom, elevator or room, it isn't on the map, and the page says it's missing. Downloads were made on 2026-10-13.

## Sources used

| Key | Source | What it gives | How exact |
|---|---|---|---|
| `osm` | [OpenStreetMap](https://www.openstreetmap.org/copyright) (ODbL), raw API 0.6 `map?bbox=` calls over campus, the Hill and the Southwest apartments, split into 42 tiles | Building outlines (mostly LA County building footprints), doors tagged `entrance=*` (some with `level`, `wheelchair`), stair doors (`entrance=staircase`), `highway=steps`, a few `highway=elevator` and `amenity=toilets` | Outlines are good to about 1 m. OSM's `level` numbers don't always match UCLA floor numbers, so the page shows them as "level N in OSM". |
| `ucla_gis` | UCLA campus map GIS, `https://map.ucla.edu/arcgis/rest/services/` (public layers, no login) | `data/location_area/MapServer/0`: building outlines. `data/data/MapServer/2`: floor counts. `data/feature/MapServer/0`: gender inclusive restrooms with building, room number, floor and position | Restroom points have distinct positions per room and stack floor over floor, so they look like room centers from UCLA's space data. |
| `c3d` | UCLA's interactive campus map, [maps.ucla.edu](https://www.maps.ucla.edu/?id=2043) (Concept3D map 2043, read through `api.concept3d.com` with the public key the web app uses) | Accessibility survey: 182 powered doors, 323 unpowered doors, 32 outdoor/entrance elevators, 138 accessible restrooms. Each has a detail record (building, door type, side of building, approach: ramp/stairs/flat, access restrictions, restroom gender) | Surveyed points. A few doors sit well inside the outline (on a bridge or podium, or placed loosely); those are drawn as approximate with a note. Some restroom points share one spot for a whole building; those are listed but not drawn. |
| `ucla_pdf` | [UCLA Campus Accessibility Map, Oct 2020 (PDF)](https://map.ucla.edu/downloads/pdf/Access_Oct_2020.pdf) | Elevator dots (purple), accessible entrance dots (green), power assisted entrance dots (red) | The PDF has no coordinates. `build_maps.py` georeferences it: an affine fit from 75 PDF building shapes to their OSM outlines (median error 6.5 m), then a local correction from nearby matched buildings. Checked against surveyed doors: the green/red dots land a median 6.8 m from the nearest surveyed or OSM door. So these are approximate, about 7 m, and the page draws them with a dashed ring. Entrance dots are only used where no surveyed or OSM door is within 15 m. |
| plan | [UCLA Library, YRL Research Commons map (2024)](https://calendar.library.ucla.edu/reserve/yrl_gsr), image `2024_Updated_-_YRL_RC_Wall_Map.png` | Group study rooms G01 to G15, collaboration pods R01 to R20, RC Classroom, Data Science Center, PDF Center, Lux Lab, Laptop Lending, restrooms, unlabeled blocks, crossed boxes (no legend; they look like elevators and are labeled that way, with the caveat) | Rooms were traced from the image by color with `trace_plan.py` (`maps/plans/YRL-1.control.json`; OCR read the labels, the pods and crossed boxes were placed by hand). The image was fitted with a similarity transform to the four all-gender restrooms UCLA's GIS places on YRL floor 1 (11938, 11940, 11368A, 11368B). Fit error 0.03 to 0.36 m at those points, rotation 0.6 degrees, 3.4 cm per pixel. The traced area reaches 1.3 m past the OSM outline on the west side. This covers only the Research Commons part of floor 1. |

The campus map tab uses OpenStreetMap tiles (`tile.openstreetmap.org`) through Leaflet 1.9.4 from cdnjs, with SRI hashes checked against the files and the cdnjs API.

The Directions button uses `https://maps.apple.com/?daddr=LAT,LON&dirflg=w` on iPhone, iPad and Mac, and `https://www.google.com/maps/dir/?api=1&destination=LAT,LON&travelmode=walking` everywhere else (Google's [Maps URLs](https://developers.google.com/maps/documentation/urls/get-started): `api=1` is required, `travelmode=walking` is documented, and the link opens the Google Maps app on Android and iOS when it's installed). Apple's current docs describe newer [unified Maps URLs](https://developer.apple.com/documentation/mapkit/unified-map-urls) (`/directions?destination=...&mode=walking`) that need iOS 18.4 or later; the older `daddr`/`dirflg=w` form is used because it also works on older iPhones. On Android there is also an "Other app" `geo:` link. The destination is the building point from `buildings.json`, or the outline's center if that point is more than 30 m off the outline.

## Traced floor plans

Rooms, corridors, stairs and walls are only drawn where a published plan exists. `trace_plan.py` traces each plan (see the top of that file for how), fits it to the ground with control points, and writes a floor record to `maps/plans/<CODE>-<floor>.json`. The control file next to it (`<CODE>-<floor>.control.json`) holds the plan's source, the control points and every hand fix, so a trace can be rerun. The table below is rewritten by `trace_plan.py` and `build_maps.py`. Residuals are the distance in metres between where the fitted plan puts each control point and where the source puts it.

<!-- plans:start -->
_Generated by trace_plan.py on 2026-10-13._

| Code | Floor | Extent | Plan | Plan date | Trace | Georeference (control point residuals) | Contents | File |
|---|---|---|---|---|---|---|---|---|
| BOELTER | 2 | full | [UCLA Space Inventory plan via github.com/tiffanyzhoujh/BoelterNavApp: UCLA Space Inventory plan of Boelter Hall floor 2](https://github.com/tiffanyzhoujh/BoelterNavApp) | 2025 screenshot | walls | similarity, plan footprint fitted to the building outline (ICP), then stretched along the building grid: outline fit: median 0.68 m, 90% within 3.66 m; UCLA restroom check: 0 of 1 inside the traced room with their number (max 3.66 m, rms None m, 0.0797 m/px) | corridor 8, elevator 5, other 30, restroom 1, room 84, stair 5; 943 wall lines; 4 flagged | `plans/BOELTER-2.json` |
| WGYOUNG | 2 | full | posted evacuation plan, floor 2, photographed 2026-10-12 | photographed 2026-10-12 | photo | similarity, sign outline fitted to the building outline (ICP), then stretched along the building grid to match its walls: outline fit: median 0.47 m, 90% within 4.63 m; UCLA restroom check: 0 of 2 inside the traced room with their number (max 4.63 m, rms None m, 0.0555 m/px) | corridor 16, elevator 1, exit 3, restroom 3, room 54, stair 12; 287 wall lines | `plans/WGYOUNG-2.json` |
| YRL | 1 | partial | [UCLA Library: "2024 Updated - YRL RC Wall Map", the map on the Research Commons room booking page](https://calendar.library.ucla.edu/reserve/yrl_gsr) | 2024 | fills | similarity, 4 points: all-gender restroom 11940 (UCLA GIS) 0.33 m; all-gender restroom 11938 (UCLA GIS) 0.36 m; all-gender restroom 11368A (UCLA GIS) 0.03 m; all-gender restroom 11368B (UCLA GIS) 0.05 m (max 0.36 m, rms 0.25 m, 0.0341 m/px) | other 8, pod 20, restroom 6, room 20, xbox 6; 0 wall lines | `plans/YRL-1.json` |
<!-- plans:end -->

## Leads that didn't give interior data

Taylor asked for rooms and corridors. These were checked and had nothing usable:

- **Concept3D (maps.ucla.edu)**: the map config (`api.concept3d.com/config?map=2043`) has `minLevel 0`, `maxLevel 0` and no level data. No indoor floors or room shapes.
- **UCLA Space Inventory** (`spaceinventory.ucla.edu`): has UCLA's floor plans (PDF and AutoCAD), but its API needs a UCLA login token. Not public, not used.
- **map.ucla.edu**: the room-level layer (`data/location`) needs a token; the public layers above are all there is. The `SPACE_BASEMAP` folder is empty to the public.
- **UCLA Library pages** for Powell, YRL, SEL, Music, Arts, Biomed and Rosenfeld: no floor maps. LibCal room pages have photos, not maps, except the YRL Research Commons map used above. A copy of an old "Powell Library Building Floorplans" PDF on a third-party site has been taken down.
- **UCLA Housing**: the old floor plan pages for the classic halls now return 403 or redirect to pages without plans. The Hill study rooms have no public plan.
- **Hedrick Study**: Architectural Record (Nov 2017) published the architects' ground floor plan with a scale bar. It doesn't number the study rooms, and it couldn't be pinned to the building outline with confidence at the published resolution, so it isn't used.
- **Capital Programs seismic reports** (e.g. Boelter 3400 Auditorium, 2020): aerial photos and structural notes only.
- **Department sites** (CS, EE, Math, the Math Circle directions page): text directions only ("MS 5200 is across from the elevator"), no plans. Text directions aren't turned into positions.
- **Building emergency / evacuation plans**: UCLA EH&S says they are posted in buildings and kept by departments; none are online for these buildings.

## Floor from the room number

UCLA rooms are mostly numbered with the floor first: Boelter 2444 is on floor 2, Math Sciences 5200 on floor 5. `floor_of()` in `build_maps.py` and `floorOf()` in `maps.js` apply that rule:

- Registrar numbers are zero padded (`02444`, `A00152`). Padding is dropped first.
- 3 or more digits: the first digit is the floor (`2444` is 2, `148` is 1, YRL `11368A` is 1).
- 1 or 2 digits: level 0 (UCLA lists Powell `29B` on floor 0). Dodd 78 and Haines 39 use this.
- A letter prefix of A, B, C or M is the floor itself (A-level, B-level). UCLA's restroom records use those letters as floor names.
- Olympic Hall numbers start with `OH` (a wing prefix, `OH107` is floor 1). CHS numbers like `AL213` start with the floor letter.
- Anything else (Young Hall `CS24`, De Neve `P350`) gives no floor. The page says it can't tell.

Each building file records how many UCLA restroom records (which list both a room number and a floor) agree with the rule. The page shows that count, and if the rule fails anywhere in a building it says "probably". Canyon Point fails (UCLA lists room 5103 on floor 1), so its floors are marked uncertain. Some people say Boelter's second digit is the hallway; that isn't from UCLA, so it isn't used to place rooms.

## Per building coverage

Doors: "survey" is UCLA's accessibility survey on maps.ucla.edu, "OSM" is OpenStreetMap, "2020 map" is the georeferenced accessibility PDF. Elevators from the 2020 map are approximate (about 7 m). The last column is how many UCLA restroom records in the building follow the room number rule.

<!-- coverage:start -->
_Generated by build_maps.py on 2026-10-13._

| Code | Outline | Doors | Elevators | Stairs | Restrooms | Rooms and corridors | Floor from room number |
|---|---|---|---|---|---|---|---|
| BOELTER | OSM (UCLA GIS 68%, UCLA map 67%) | 12 (survey 4, OSM 7, 2020 map, approx. 1) | 4 (2020 map, approx. 4) | 7 (stair doors 3, outdoor steps 4) | 24 (UCLA GIS 4, survey 20) | missing (no public plan found) | 4/4 |
| BROAD | OSM (UCLA GIS 84%, UCLA map 90%) | 9 (survey 9) | missing | missing (no public source has indoor stairwells) | 2 (UCLA GIS 2) | missing (no public plan found) | 2/2 |
| BUNCHE | OSM (UCLA GIS 75%, UCLA map 86%) | 7 (survey 3, OSM 3, 2020 map, approx. 1) | 2 (2020 map, approx. 1, survey 1) | 1 (outdoor steps 1) | 12 (UCLA GIS 9, survey 3) | missing (no public plan found) | 10/10 |
| DODD | OSM (UCLA GIS 80%, UCLA map 92%) | 6 (survey 6) | 1 (2020 map, approx. 1) | missing (no public source has indoor stairwells) | 8 (survey 8) | missing (no public plan found) | 1/1 |
| FOWLER | OSM (UCLA GIS 94%, UCLA map 84%) | 2 (survey 1, OSM 1) | 2 (2020 map, approx. 1, survey 1) | missing (no public source has indoor stairwells) | missing | missing (no public plan found) | no UCLA floor records |
| FRANZ | UCLA GIS (UCLA map 78%) | 4 (survey 2, OSM 2) | 1 (2020 map, approx. 1) | 5 (stair doors 2, outdoor steps 3) | 2 (UCLA GIS 1, survey 1) | missing (no public plan found) | 1/1 |
| GEOLOGY | UCLA GIS (UCLA map 74%) | 6 (survey 2, OSM 4) | 1 (2020 map, approx. 1) | 2 (stair doors 2) | 3 (UCLA GIS 1, survey 2) | missing (no public plan found) | 1/1 |
| HAINES | OSM (UCLA GIS 86%, UCLA map 85%) | 7 (survey 7) | 1 (2020 map, approx. 1) | 5 (stair doors 2, outdoor steps 3) | 2 (survey 2) | missing (no public plan found) | no UCLA floor records |
| KAPLAN | OSM (UCLA GIS 91%, UCLA map 83%) | 6 (survey 2, OSM 4) | 1 (2020 map, approx. 1) | 8 (stair doors 4, outdoor steps 4) | 5 (survey 5) | missing (no public plan found) | 1/1 |
| KAUFMAN | OSM (UCLA GIS 89%, UCLA map 89%) | 3 (survey 2, OSM 1) | 1 (2020 map, approx. 1) | missing (no public source has indoor stairwells) | 1 (UCLA GIS 1) | missing (no public plan found) | 1/1 |
| KNSY PV | OSM | 3 (survey 3) | missing | missing (no public source has indoor stairwells) | missing | missing (no public plan found) | no UCLA floor records |
| LAKRETZ | OSM (UCLA GIS 80%, UCLA map 81%) | 2 (survey 1, OSM 1) | 1 (survey 1) | 2 (stair doors 1, outdoor steps 1) | 2 (UCLA GIS 2) | missing (no public plan found) | 2/2 |
| MOORE | OSM (UCLA GIS 94%, UCLA map 83%) | 6 (survey 2, OSM 4) | 1 (2020 map, approx. 1) | 6 (stair doors 2, outdoor steps 4) | 3 (survey 3) | missing (no public plan found) | 1/1 |
| MS | OSM (UCLA GIS 75%, UCLA map 74%) | 9 (survey 3, OSM 6) | 2 (2020 map, approx. 2) | 6 (stair doors 1, outdoor steps 5) | 7 (UCLA GIS 2, survey 5) | missing (no public plan found) | 2/2 |
| PAB | OSM (UCLA GIS 90%, UCLA map 85%) | 5 (survey 2, OSM 3) | 1 (2020 map, approx. 1) | 1 (outdoor steps 1) | 6 (survey 6) | missing (no public plan found) | no UCLA floor records |
| PERLOFF | OSM (UCLA GIS 75%, UCLA map 89%) | 7 (survey 6, OSM 1) | 1 (2020 map, approx. 1) | 2 (stair doors 1, outdoor steps 1) | 1 (survey 1) | missing (no public plan found) | no UCLA floor records |
| PUB AFF | OSM (UCLA GIS 87%, UCLA map 86%) | 8 (survey 5, OSM 2, 2020 map, approx. 1) | 1 (2020 map, approx. 1) | missing (no public source has indoor stairwells) | 12 (UCLA GIS 2, survey 10); 3 more listed without a position | missing (no public plan found) | 4/4 |
| ROLFE | OSM (UCLA GIS 85%, UCLA map 91%) | 11 (survey 9, OSM 2) | 1 (2020 map, approx. 1) | 2 (outdoor steps 2) | 1 (survey 1) | missing (no public plan found) | no UCLA floor records |
| ROYCE | OSM (UCLA GIS 92%, UCLA map 91%) | 5 (survey 3, OSM 2) | 1 (2020 map, approx. 1) | 1 (outdoor steps 1) | 5 (UCLA GIS 1, survey 4) | missing (no public plan found) | 1/1 |
| SLICHTR | UCLA GIS (UCLA map 71%) | missing | missing | missing (no public source has indoor stairwells) | 4 (UCLA GIS 4) | missing (no public plan found) | 5/5 |
| WGYOUNG | OSM (UCLA GIS 88%, UCLA map 89%) | 11 (survey 8, OSM 3) | 2 (2020 map, approx. 1, survey 1) | 4 (stair doors 2, outdoor steps 2) | 9 (UCLA GIS 1, survey 8) | missing (no public plan found) | 1/1 |
| COVEL | OSM (UCLA GIS 86%, UCLA map 80%) | 3 (survey 3) | 2 (2020 map, approx. 1, survey 1) | missing (no public source has indoor stairwells) | 1 (UCLA GIS 1) | missing (no public plan found) | 1/1 |
| DE NEVE | OSM (UCLA GIS 85%, UCLA map 91%) | 3 (survey 3) | missing | missing (no public source has indoor stairwells) | missing | missing (no public plan found) | no UCLA floor records |
| CARNESL | UCLA GIS (UCLA map 52%) | 9 (survey 7, 2020 map, approx. 2) | 1 (2020 map, approx. 1) | 2 (outdoor steps 2) | missing | missing (no public plan found) | no UCLA floor records |
| HEDRICK | OSM (UCLA GIS 78%, UCLA map 87%) | 5 (survey 4, 2020 map, approx. 1) | 2 (2020 map, approx. 2) | missing (no public source has indoor stairwells) | 5 (UCLA GIS 5) | missing (no public plan found) | 5/5 |
| RIEBER | OSM (UCLA GIS 81%, UCLA map 84%) | 6 (survey 6) | 2 (2020 map, approx. 2) | missing (no public source has indoor stairwells) | 5 (UCLA GIS 5) | missing (no public plan found) | 5/5 |
| SPROUL | UCLA GIS (UCLA map 75%) | 7 (survey 6, 2020 map, approx. 1) | 2 (2020 map, approx. 2) | missing (no public source has indoor stairwells) | 6 (UCLA GIS 6) | missing (no public plan found) | 6/6 |
| HITCH | OSM (UCLA GIS 38%, UCLA map 76%) | 4 (survey 3, 2020 map, approx. 1) | missing | missing (no public source has indoor stairwells) | 2 (UCLA GIS 2) | missing (no public plan found) | 2/2 |
| OLYMPIC | OSM (UCLA GIS 83%, UCLA map 82%) | missing | missing | missing (no public source has indoor stairwells) | 13 (UCLA GIS 13) | missing (no public plan found) | 13/13 |
| CNYN PT | OSM (UCLA GIS 68%, UCLA map 83%) | 9 (survey 9) | 2 (survey 2) | missing (no public source has indoor stairwells) | 1 (UCLA GIS 1) | missing (no public plan found) | 0/1 |
| GAYLEY HEIGHTS | OSM (UCLA GIS 48%, UCLA map 88%) | missing | missing | missing (no public source has indoor stairwells) | missing | missing (no public plan found) | no UCLA floor records |
| TIPUANA | UCLA GIS (UCLA map 42%) | missing | missing | missing (no public source has indoor stairwells) | missing | missing (no public plan found) | no UCLA floor records |
| LAUREL | OSM (UCLA GIS 64%, UCLA map 84%) | 1 (2020 map, approx. 1) | missing | missing (no public source has indoor stairwells) | missing | missing (no public plan found) | no UCLA floor records |
| PALOS VERDE | UCLA GIS (UCLA map 51%) | 1 (2020 map, approx. 1) | missing | missing (no public source has indoor stairwells) | missing | missing (no public plan found) | no UCLA floor records |
| POWELL | OSM (UCLA GIS 92%, UCLA map 88%) | 6 (survey 3, OSM 2, 2020 map, approx. 1) | 3 (2020 map, approx. 2, survey 1) | 5 (stair doors 2, outdoor steps 3) | 3 (survey 3); 4 more listed without a position | missing (no public plan found) | 2/2 |
| YRL | OSM (UCLA GIS 83%, UCLA map 94%) | 2 (survey 1, OSM 1) | 1 (2020 map, approx. 1) | 2 (outdoor steps 2) | 5 (UCLA GIS 4, survey 1) | floor 1: 40 spaces from UCLA Library (2024), fit max 0.36 m; 35 numbered rooms placed | 4/4 |
| CHS | OSM (UCLA GIS 61%, UCLA map 70%) | 18 (survey 6, OSM 4, 2020 map, approx. 8) | 2 (2020 map, approx. 2) | 1 (outdoor steps 1) | 8 (UCLA GIS 8) | missing (no public plan found) | 8/8 |
| SCHOENBG | OSM (UCLA GIS 91%, UCLA map 82%) | 12 (survey 9, OSM 2, 2020 map, approx. 1) | 1 (2020 map, approx. 1) | missing (no public source has indoor stairwells) | 4 (survey 4) | missing (no public plan found) | 2/2 |
| ROSENFLD | OSM (UCLA GIS 40%, UCLA map 77%) | 4 (survey 3, 2020 map, approx. 1) | 1 (2020 map, approx. 1) | missing (no public source has indoor stairwells) | missing | missing (no public plan found) | no UCLA floor records |
<!-- coverage:end -->

## Rebuild

```bash
pip install pdfplumber
python maps/build_maps.py            # uses maps/.cache if it's there (git-ignored)
python maps/build_maps.py --refresh  # download everything again
```

To add a traced plan, write a control file in `maps/plans/` like `YRL-1.control.json` (building code, floor, source URL and date, 2 to 4 control points pairing plan pixels with something a source above places, like a UCLA restroom or a corner of the outline, and any hand fixes) and run `python maps/trace_plan.py` on it. It writes the floor record `maps/plans/<CODE>-<floor>.json`, which `build_maps.py` merges on every rebuild.
