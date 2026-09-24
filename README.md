# StudySpace

Find empty UCLA classrooms to study in. The site shows every general assignment classroom, whether it's free right now (campus time), and its week of classes and club/org events.

## What's in here

- `index.html`: the whole frontend. It loads `classrooms.json` and does filtering, sorting and rendering in the browser.
- `classrooms.json`: the data the site reads.
- `scrape.py`: pulls class schedules and room characteristics from the UCLA Registrar.
- `scrape_events.py`: pulls club / org / department events from [UCLA Community](https://community.ucla.edu/calendars) and attaches the ones held in our classrooms.
- `generate_urls.py`: rebuilds the classroom list and registrar URLs from scratch. You only need this if the room list changes.
- `add_images.py`, `download_images.py`: match room photos from UCLA DTS and cache them in `images/`.

## Data sources

**Classes.** The registrar's ClassroomDetail page embeds the full term calendar as JSON in the HTML, so `scrape.py` uses plain HTTP requests (no browser). All 190 rooms take about 30 to 60 seconds.

**Club bookings.** The registrar grids leave out non-academic events. Per the registrar page: "Non-academic events in General Assignment classrooms do not appear on these classroom grids." Reservations made through the UCLA Events Office are not published anywhere public (no public 25Live/EMS instance). The only open source is UCLA Community, so `scrape_events.py` reads every week of the term there and matches event locations like "Boelter 2444" or "Math Sciences Building, Room 5200" to our rooms. The site shows these in orange on the calendar and counts them when deciding whether a room is free. Coverage only includes orgs that post events there with a room number, so a room shown as free can still be booked.

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
- `.github/workflows/static.yml` deploys to GitHub Pages on pushes to `master` and after every data update. Pushes made with the Actions token don't fire other workflows on their own, so it listens for `workflow_run`.

## Etiquette

Educational use. The scraper makes about 190 requests with 8 at a time, once a day. Keep it around that.

---

Built and maintained by Taylor Nilsen. https://github.com/Taylor-Nilsen
