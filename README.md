# StudySpace

Find empty UCLA classrooms to study in. The site shows every general assignment classroom, whether it's free right now (campus time), and its week of classes and club/org events. It also shows Hill study rooms, libraries, library study rooms and computer labs.

## What's in here

- `index.html`: the whole frontend. It loads `classrooms.json` and does filtering, sorting and rendering in the browser.
- `maps.js`, `maps/`: building floor maps and the campus map (see Maps below). `maps/build_maps.py` builds the data.
- `classrooms.json`: the data the site reads.
- `scrape.py`: pulls class schedules and room characteristics from the UCLA Registrar.
- `scrape_hill.py`: pulls Hill study room availability from Residential Life into `hill.json`.
- `scrape_library.py`: pulls library hours, library study room bookings and computer lab hours into `library.json`.
- `hill_api.py`: live relay for Hill and library availability, deployed on Render.
- `buildings.json`: every building code with its name, area (campus, Hill, ...) and location.
- `scrape_events.py`: pulls club / org / department events from [UCLA Community](https://community.ucla.edu/calendars) and attaches the ones held in our classrooms.
- `generate_urls.py`: rebuilds the classroom list and registrar URLs from scratch. You only need this if the room list changes.
- `add_images.py`, `download_images.py`: match room photos from UCLA DTS and cache them in `images/`.

## Data sources

**Classes.** The registrar's ClassroomDetail page embeds the full term calendar as JSON in the HTML, so `scrape.py` uses plain HTTP requests (no browser). All 190 rooms take about 30 to 60 seconds.

**Club and department events.** The registrar grids leave out non-academic events. Per the registrar page: "Non-academic events in General Assignment classrooms do not appear on these classroom grids." Clubs request rooms from the UCLA Events Office through a form, and those reservations are not published anywhere public (no public 25Live/EMS instance). `scrape_events.py` reads the public calendars that do list rooms:

- [UCLA Community](https://community.ucla.edu/calendars): every term week, every program page, and club sports.
- About 25 department iCal feeds (`/events/?ical=1`), including CS, CEE, MAE, Bioengineering, ChemE, MSE, Samueli, Chemistry, Economics, History, Luskin, Philosophy and Linguistics. The list is `ICAL_FEEDS` in the script.

It keeps events whose location names one of our rooms ("3400 Boelter Hall", "Math Sciences Building, Room 5200", "Kaufman Hall 101 & 136"). The site shows them in orange on the calendar and counts them when deciding whether a room is free. Anything booked without being posted to one of these calendars is still invisible, so a room shown as free can be taken.

**Short gaps.** A gap under an hour between two bookings counts as busy. It shows as a hatched block on the calendar.

**Time window.** "Show rooms free between" keeps a room if it has at least an hour free inside the window (or the whole window, when the window is shorter than an hour). A room that is only partly free says which part on its card. Before, a single half hour booking anywhere in the window hid the room, which made bookable library rooms disappear in the evening.

**The Hill.** A Campus / The Hill / Libraries / All switch at the top picks where to look. Two kinds of Hill rooms show up under The Hill:

- Registrar rooms in Hill buildings that have classes this term (Covel 210/218/225/319A, De Neve P350). `scrape.py` scrapes these along with the general assignment rooms. `--all` scrapes every registrar room.
- Residential study rooms (Hedrick, The Study at Hedrick, Rieber, Sproul, Olympic, Southwest Apartments, Gayley Heights) from [Residential Life reservations](https://reserve.reslife.ucla.edu/reserve). That site publicly lists every open hourly slot for the next two weeks, so `scrape_hill.py` writes `hill.json` with each room's open hours and free slots. Anything inside open hours that isn't listed is reserved. Only residents can book these rooms. Covel and Carnesale study spaces are not on that site.
- Open study spaces that Residential Life lists with hours under "Study Spaces on the Hill" on its [academics page](https://reslife.ucla.edu/academics): Covel 227, Sproul Landing Living Room, Hedrick Study, Olympic Hall Understory & Canopy, Sunset Village Learning Center (150) and De Neve Learning Center (P333). No reservation needed. The hours are only posted as text, so they live in `HILL_SPACES` in `scrape_hill.py`, which adds them to `hill.json`. Every run checks that the posted text is still on the page and leaves a space out if it changed, so update the list when that happens. Epicuria at Covel's late night study ("Open Week 4, Sunday through Thursday 10 PM to 2 AM") is left out because the page doesn't say which dates that is. Gayley Heights, Laurel, Tipuana and Palo Verde are University Apartments, not the Hill (Residential Life lists them as off campus), so their rooms show under The Hill with a "University Apartments" badge instead of "The Hill".

Reservations change constantly, so the page pulls them live every time it opens (and every 5 minutes while open). The booking site sends no CORS headers, so the browser can't read it directly. `hill_api.py` is a small relay on Render (`https://uclastudyspace-hill.onrender.com/hill`). It runs the same scraper, caches the result for 60 seconds, and returns JSON with CORS enabled. If its last read is under 15 minutes old, it answers immediately with that read and refreshes in the background, since the booking site sometimes takes a minute or more to answer. Pages that fail to load are left out rather than shown as booked, and the page fills those gaps from the snapshot. The page shows the bundled `hill.json` snapshot immediately, then swaps in the live data. The header says which one you're seeing ("live, updated just now" or "snapshot from 20 min ago"). The Pages workflow still refreshes the snapshot before every deploy, every 30 minutes.

The relay runs on Render's free plan, which sleeps after 15 minutes idle, so the first visit after a quiet spell can take up to a minute to go live. The snapshot shows in the meantime.

Render settings: runtime Python, build `pip install -r requirements.txt`, start `python hill_api.py`, branch `master`. The same relay serves `/library`.

**Where buildings are.** `buildings.json` is the one list of which building is on campus and which is on the Hill. The page, `scrape.py` and `scrape_hill.py` all read it, so a building only has to be fixed in one place. Each entry has a `source` saying where its name, area and location came from:

- Names: the [Registrar building list](https://registrar.ucla.edu/faculty-staff/classrooms-and-scheduling/building-list).
- The Hill: Residential Life's [communities list](https://reslife.ucla.edu/communities) (Sunset Village, Olympic/Centennial, De Neve, Dykstra, Hedrick, Hitch, Rieber, Saxon, Sproul), its "Study Spaces on the Hill" (Covel), [UCLA Conferences](https://conferences.ucla.edu/building-locations/) (Carnesale Commons, Northwest Campus Auditorium) and the Wikipedia UCLA Housing article (Bradley Hall).
- Areas: `campus`, `hill`, `apartments` (University Apartments), `offsite` (Clark Library) and `none` (ONLINE).
- Locations: centroids from OpenStreetMap map data. Carnesale Commons isn't in OpenStreetMap, so it uses the map pin that UCLA Conferences links to.

Covel was always tagged as Hill in the code. What made it look like campus was the Building filter: it listed every building in one list no matter which area was picked, so Covel, De Neve and the Hill dorms showed up as options under Campus. The filter now only lists buildings in the picked area, and under All it groups them by area.

**Libraries and labs.** The Libraries button shows:

- Whole libraries with their hours from [UCLA Library](https://calendar.library.ucla.edu/hours): Powell, Night Powell, YRL, East Asian, Biomedical, Biomedical Study Commons, SEL Boelter, SEL Geology, Arts, Music, Rosenfeld and Law. The Law Library is limited access for non-law students (same day reservation).
- Library study rooms from [LibCal spaces](https://calendar.library.ucla.edu/spaces): Powell group rooms, CLICC loop booths, YRL pods and group rooms, Music seminar room, Biomedical rooms, SEL rooms and the Powell Media Lab rooms. LibCal's availability grid lists every bookable 30 minute slot in the booking window (3 days for CLICC rooms, 7 for others); a booked slot shows as reserved. Past the window nobody can book yet, so the room counts as free during library hours. CLICC rooms are drop-in when nobody has them.
- CLICC classrooms (Powell 307, 320, 320B, YRL 11630F): open for drop-in study when not booked. Bookings come from the CLICC classrooms calendar.
- The CLICC Lab in Powell 145.

SEASnet computer labs (Boelter 2664, 3760, 4404, 4405, 4442) show under Campus with type "Computer lab". Hours come from the [SEASnet labs page](https://www.seasnet.ucla.edu/seasnet-computer-labs/). You need a SEASnet account, which engineering students and anyone in an engineering course can get. 3760 and 4404 also hold classes; `scrape.py` scrapes those two rooms so the classes show. Social Sciences Computing and Luskin labs are not included: SSC's posted hours are from Fall 2025 and its class schedule isn't public, and the Luskin lab needs a Luskin account.

`scrape_library.py` makes about 17 requests per run, one at a time. The Pages workflow runs it before every deploy, and the relay caches it for 5 minutes.

**Nearest to me.** The "📍 Nearest to me" button is a toggle: gray when off, blue when on. Turning it on asks the browser for your location (the first time only; a fix from the last 5 minutes is reused), loads `buildings.json` and sorts rooms by straight-line distance to their building, with a rough walking time. Turning it off, or picking another sort, goes back to the normal sort. Nothing is requested before you press it, and your location is never sent or stored anywhere. `buildings.json` holds building centroids from OpenStreetMap (see "Where buildings are").

If it can't get your location, the message next to the button says why and where to fix it. Safari notes:

- iPhone and iPad: Settings > Privacy & Security > Location Services has to be on, and Safari Websites under it set to While Using the App (or Ask Next Time). Settings > Apps > Safari > Location has to be Ask or Allow, not Deny.
- Mac: Safari > Settings > Websites > Location has to be Ask or Allow for this site, and Safari has to be on in System Settings > Privacy & Security > Location Services.
- After you change any of these, reload the page. Once Safari says no, it keeps saying no until the page is reloaded.
- If the prompt never shows up, the button says "Locating…" and nags after a few seconds. Tap it again to cancel.
- Macs have no GPS, so if a precise fix takes too long it falls back to a rough Wi-Fi one. Wi-Fi has to be on.
- Links opened inside Instagram, TikTok and similar apps use the app's own browser, which usually can't get location. Open the page in Safari.

`window.StudyLocate` is the one piece of code that asks for location, so other features (like the map) reuse the same fix and error messages: `get()` returns `{lat, lon, accuracy, at}` and has to be called straight from a tap the first time, `watch(onFix, onError)` gives live updates and returns a stop function, `last()` is the latest fix or null, and `explainError(err)` turns an error into the message above.

**Maps.** Every room card has a "🗺️ Map" button. It opens a popup with two tabs:

- **Floor**: the building drawn like the fire escape plan on the wall, on a light "paper" panel (a ☾ Dark button switches the plan to dark colors and the choice is remembered). Thick black outer walls, thinner inside walls, every room as a box with its number, corridors left open, stairs with treads, elevators with the up/down sign, restrooms in blue, exits as green EXIT tags with the door swing, a north arrow, a scale bar in meters and feet, and a legend of what's on the plan. Your room is filled yellow. Pick a floor at the top (▦ marks floors that have a traced plan). Zoom with + and −, a pinch, ctrl+scroll or a double click, and drag to move; ⤢ shows the whole building. The room's floor comes from its number (Boelter 2444 is on floor 2), and the page says how sure that is. "📍 Show me" puts a red YOU ARE HERE marker where you are, with your GPS accuracy around it (an arrow at the edge when you're off the part shown), and tells you if you're outside the building and how far away. GPS can't tell what floor you're on.
- **Campus**: an OpenStreetMap map with the building in blue, the other StudySpace buildings outlined, and your position if location is on. Leaflet only loads the first time you open this tab.

"Directions" opens walking directions in Apple Maps on iPhone, iPad and Mac, and in Google Maps everywhere else.

What's accurate and what isn't:

- Outlines are from OpenStreetMap or UCLA's campus GIS, good to about a meter. Each building says how well the two agree.
- Doors are from UCLA's accessibility survey on maps.ucla.edu and from OpenStreetMap. Restrooms are from UCLA's GIS and the same survey, with room numbers.
- Most elevators come from UCLA's 2020 accessibility map PDF, which has no coordinates. The script lines it up with the building outlines, and its dots end up about 7 m from where they really are. Those show with a dashed ring and say "approximate".
- Rooms, corridors, stairs and walls are only drawn where a published plan exists. `maps/trace_plan.py` traces the plan (an image or a PDF page) into rooms and walls and lines it up with the ground using 2 to 4 control points; `maps/SOURCES.md` lists every traced floor with its source, date and fit error, and the map names the plan and its date under the drawing. Right now that's the YRL Research Commons (group study rooms G01 to G15 and the pods), traced from the UCLA Library's map. UCLA keeps real floor plans in its Space Inventory, but that needs a UCLA login. A floor with no plan shows the outline, doors, elevators and restrooms, and the page says "room position not mapped" instead of guessing.

`maps/SOURCES.md` lists every source, how each one was lined up, the leads that didn't pan out, and a table of what's known for each building. To rebuild the map data:

```bash
pip install pdfplumber
python maps/build_maps.py            # add --refresh to download everything again
```

To trace a plan, write a control file next to the others in `maps/plans/` and run it (see the top of `trace_plan.py` for the format; it needs `pip install opencv-python-headless scikit-image scipy pypdfium2 pdfplumber pytesseract` and the `tesseract` program for scanned plans):

```bash
python maps/trace_plan.py maps/plans/YRL-1.control.json --debug /tmp/trace   # writes maps/plans/YRL-1.json and updates maps/YRL.json
python maps/trace_plan.py --render plan.pdf --page 2 --out grid.png          # a pixel grid for picking control points
```

Downloads go to `maps/.cache` (git-ignored). Only the small JSON files in `maps/` get committed. `maps.js` is all the page code for the maps; it does nothing until you press a Map button.

## Data contract

`classrooms.json` is an array. The frontend only uses rooms with `offered: true`:

```json
{
  "text": "BOELTER  2444",
  "building": "BOELTER",
  "room": "02444",
  "offered": true,
  "capacity": 80,
  "type": "Classroom",
  "url": "https://sa.ucla.edu/ro/Public/SOC/Results/ClassroomDetail?term=26F&classroom=...",
  "image_url": "images/Boelter-2444.jpeg",
  "characteristics": ["Air Conditioning", "Chalkboard"],
  "schedule": {
    "Monday": [{"course": "COM SCI 31", "type": "LEC 1", "start_time": "10:00 AM", "end_time": "11:50 AM", "enrolled": 245, "capacity": 250}]
  },
  "no_calendar": false,
  "events": [
    {"title": "Club GM", "org": "Some Club", "url": "https://community.ucla.edu/event/...", "date": "2026-10-05", "start_time": "06:00 PM", "end_time": "08:00 PM"}
  ]
}
```

`schedule` is weekly and repeats. `events` are one-off and dated. `image_url`, `schedule` and `events` are optional.

## Run locally

```bash
python3 -m http.server 8000   # then open http://localhost:8000
```

Browsers block `fetch()` on `file://`, so you need a local server.

## Update the data

```bash
pip install -r requirements.txt
python scrape.py              # term auto-detected; or --term 26F, --limit N, --workers N
python scrape_events.py       # same --term flag
```

The term is chosen from the date, switching about 2 weeks before each quarter starts. `scrape.py` won't save if more than half the rooms fail.

## Automation

- `.github/workflows/update-classrooms.yml` runs daily at 6 AM PT (and on demand, with an optional term). It scrapes both sources and commits `classrooms.json` only if something changed.
- `.github/workflows/static.yml` deploys to GitHub Pages on pushes to `master` and after every data update. Pushes made with the Actions token don't fire other workflows on their own, so it listens for `workflow_run`. It refreshes `hill.json` and `library.json` before each deploy.

## Etiquette

Educational use. The scraper makes about 220 requests with 8 at a time, once a day. Keep it around that. `scrape_library.py` makes about 17, one at a time.

---

Built and maintained by Taylor Nilsen. https://github.com/Taylor-Nilsen
