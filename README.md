# StudySpace

Find somewhere to study at UCLA: empty classrooms, Hill study rooms, library study rooms, libraries, labs, lounges and cafés, and whether each is free or open right now.

## What's in here

- `index.html`: the whole frontend. It loads `classrooms.json`, `hill.json`, `library.json` and `spaces.json` and does filtering, sorting and rendering in the browser.
- `classrooms.json`: the classroom data the site reads.
- `scrape.py`: pulls class schedules and room characteristics from the UCLA Registrar.
- `scrape_hill.py`: pulls Hill study room availability from Residential Life into `hill.json`.
- `scrape_library.py`: pulls UCLA Library hours and bookable group study room availability into `library.json`.
- `hill_api.py`: live relay for Hill and Library availability (two feeds), deployed on Render.
- `scrape_events.py`: pulls club / org / department events from [UCLA Community](https://community.ucla.edu/calendars) and attaches the ones held in our classrooms.
- `generate_urls.py`: rebuilds the classroom list and registrar URLs from scratch. You only need this if the room list changes.
- `add_images.py`, `download_images.py`: match room photos from UCLA DTS and cache them in `images/`.
- `spaces.json`: a curated list of libraries, computer labs, makerspaces, study lounges, student union spots, cafés and outdoor spots that aren't bookable classrooms or Hill rooms.
- `buildings.json`: building centroids used for "Nearest to me".

## Data sources

**Classes.** The registrar's ClassroomDetail page embeds the full term calendar as JSON in the HTML, so `scrape.py` uses plain HTTP requests (no browser). All 190 rooms take about 30 to 60 seconds.

**Club and department events.** The registrar grids leave out non-academic events. Per the registrar page: "Non-academic events in General Assignment classrooms do not appear on these classroom grids." Clubs request rooms from the UCLA Events Office through a form, and those reservations are not published anywhere public (no public 25Live/EMS instance). `scrape_events.py` reads the public calendars that do list rooms:

- [UCLA Community](https://community.ucla.edu/calendars): every term week, every program page, and club sports.
- About 25 department iCal feeds (`/events/?ical=1`), including CS, CEE, MAE, Bioengineering, ChemE, MSE, Samueli, Chemistry, Economics, History, Luskin, Philosophy and Linguistics. The list is `ICAL_FEEDS` in the script.

It keeps events whose location names one of our rooms ("3400 Boelter Hall", "Math Sciences Building, Room 5200", "Kaufman Hall 101 & 136"). The site shows them in orange on the calendar and counts them when deciding whether a room is free. Anything booked without being posted to one of these calendars is still invisible, so a room shown as free can be taken.

**Short gaps.** A gap under an hour between two bookings counts as busy. It shows as a hatched block on the calendar.

**The Hill.** A Campus / The Hill / Both switch at the top picks where to look. Two kinds of Hill rooms show up under The Hill:

- Registrar rooms in Hill buildings that have classes this term (Covel 210/218/225/319A, De Neve P350). `scrape.py` scrapes these along with the general assignment rooms. `--all` scrapes every registrar room.
- Residential study rooms (Hedrick, The Study at Hedrick, Rieber, Sproul, Olympic, Southwest Apartments, Gayley Heights) from [Residential Life reservations](https://reserve.reslife.ucla.edu/reserve). That site publicly lists every open hourly slot for the next two weeks, so `scrape_hill.py` writes `hill.json` with each room's open hours and free slots. Anything inside open hours that isn't listed is reserved. Only on-campus residents can book these rooms. Covel and Carnesale study spaces are not on that site.

Reservations change constantly, so the page pulls them live every time it opens (and every 5 minutes while open). The booking site sends no CORS headers, so the browser can't read it directly. `hill_api.py` is a small relay on Render that serves two feeds, `https://uclastudyspace-hill.onrender.com/hill` and `.../library`. Each has its own cache: it runs the matching scraper (`scrape_hill.collect` / `scrape_library.collect`), caches the result for 60 seconds, and returns JSON with CORS enabled. If a feed's last read is under 15 minutes old, it answers immediately with that read and refreshes in the background, since the booking sites sometimes take a minute or more to answer. Pages that fail to load are left out rather than shown as booked, and the page fills those gaps from the snapshot. The page shows the bundled `hill.json` / `library.json` snapshots immediately, then swaps in the live data. The header says which one you're seeing ("live, updated just now" or "snapshot from 20 min ago"). The Pages workflow still refreshes both snapshots before every deploy, every 30 minutes.

The relay runs on Render's free plan, which sleeps after 15 minutes idle, so the first visit after a quiet spell can take up to a minute to go live. The snapshots show in the meantime.

Render settings: runtime Python, build `pip install -r requirements.txt`, start `python hill_api.py`, branch `master`.

**Libraries.** `scrape_library.py` pulls from the UCLA Library's LibCal site (calendar.library.ucla.edu), two public feeds, no login needed:

- Hours: `api_hours_grid.php` lists open hours per date for the next couple of weeks for every library and department (Powell, YRL, Night Powell, CLICC lab, Biomedical Study Commons, ...). `spaces.json` entries point at one of these locations with an `hours_lid`, so a library card's hours come straight from this feed. A day can be open all day, closed, or unposted (the LibCal status is neither "open" nor "closed" nor "24hours"); unposted hours show as "Hours not posted" rather than a guess.
- Bookable group study rooms: each library's `/spaces` page lists its rooms with capacities, and `/spaces/availability/grid` returns every 30 minute slot for a date range, flagged when booked. Slots only exist inside booking hours, so the union of a day's slots is that day's open window and a missing slot inside it is reserved. The LibCal booking window only opens about 3 days out, so `scrape_library.py` defaults to `--days 4`. Anyone with a UCLA Logon (not just current UCLA affiliates with special access) can book a library study room, unlike the Hill rooms.

`library.json` has the same room shape as `hill.json` (`days: {date: {hours: [open, close], free: [[a, b], ...]}}`), plus an `hours` map keyed by LibCal location id (`days: {date: [[open, close], ...] | [] | null}`, `null` meaning hours weren't posted for that date).

**Libraries, labs, lounges and other spaces.** `spaces.json` is a curated list of 56 spots that aren't bookable classrooms or Hill rooms: libraries, computer labs, makerspaces, study lounges, student union spots, cafés and a few outdoor spots. Each entry gets its hours from one of four places: a library's `hours_lid` (live from `library.json`'s hours map, see above), an `asucla` block fetched straight from ASUCLA's mobile app hours API (`https://mobileapp.asucla.ucla.edu:3004/api/property/<group>`, the same one `asucla.ucla.edu/hours` uses; it sends CORS headers so the browser can read it directly, no relay needed, and the static `hours` in the entry stay if that fetch fails), static weekly `hours` (a `{Monday: [["8:00 AM", "5:00 PM"]], ...}` schedule that doesn't change week to week), or nothing at all, in which case the card just says "Hours not posted". Most entries come from official UCLA pages (Samueli, SEASnet, Residential Life, Housing, CPO, HumTech, SSC, Luskin, LS Core, UCLA Recreation, ASUCLA, Transfer Student Center, Hammer Museum, Botanical Garden); the outdoor spots and a few lounges come from BruinLife and Daily Bruin write-ups and have no posted hours.

**Nearest to me.** The "📍 Nearest to me" button is a toggle: gray when off, blue when on. Turning it on asks the browser for your location (the first time only; a fix from the last 5 minutes is reused), loads `buildings.json` and sorts rooms by straight-line distance to their building, with a rough walking time. Turning it off, or picking another sort, goes back to the normal sort. Nothing is requested before you press it, and your location is never sent or stored anywhere. `buildings.json` holds building centroids from OpenStreetMap (Nominatim / Overpass), now including the buildings the library rooms and other spaces added. Carnesale Commons is not in OpenStreetMap, so it has no coordinates; add them if its rooms start showing up.

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

`library.json` rooms follow the same shape as `hill.json` rooms: `days: {date: {hours: [open, close], free: [[a, b], ...]}}`, minutes since midnight. It also carries an `hours` map keyed by LibCal location id, `days: {date: [[open, close], ...] | [] | null}`.

`spaces.json` is `{updated, sources, spaces}`. Each entry in `spaces` is:

```json
{
  "text": "Powell Library",
  "building": "POWELL",
  "room": "2408, 2410, 2412",
  "category": "Library",
  "area": "library",
  "hours_lid": 2572,
  "access": "UCLA students, faculty and staff; physical BruinCard for CLICC loans",
  "description": "Main undergraduate library...",
  "characteristics": ["Quiet", "Outlets", "Printing"],
  "url": "https://www.library.ucla.edu/visit/locations/powell-library/",
  "reserve_url": "https://calendar.library.ucla.edu/spaces?lid=4361"
}
```

`building` and `room` are optional. `area` is `library`, `space` or `hill`. `hours` comes from exactly one of `hours_lid` (a `library.json` location id), an `asucla` block (`{group, match}`, fetched live from ASUCLA), a static weekly `hours` object, or nothing, in which case the card shows "Hours not posted" (an optional `hours_note` explains why, e.g. "By request or appointment"). `reserve_url` is optional and only set for spots you can book ahead.

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
python scrape_library.py      # UCLA Library hours and study room availability; or --days N, --file
```

The term is chosen from the date, switching about 2 weeks before each quarter starts. `scrape.py` won't save if more than half the rooms fail.

## Automation

- `.github/workflows/update-classrooms.yml` runs daily at 6 AM PT (and on demand, with an optional term). It scrapes both sources and commits `classrooms.json` only if something changed.
- `.github/workflows/static.yml` deploys to GitHub Pages on pushes to `master` and after every data update. Pushes made with the Actions token don't fire other workflows on their own, so it listens for `workflow_run`. It also runs every 30 minutes on a schedule to refresh the committed `hill.json` and `library.json` snapshots and redeploy, since both change by the minute. Each refresh is best effort: if a scrape fails, that step is skipped and the previously committed snapshot ships.

## Etiquette

Educational use. The scraper makes about 190 requests with 8 at a time, once a day. Keep it around that.

---

Built and maintained by Taylor Nilsen. https://github.com/Taylor-Nilsen
