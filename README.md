# StudySpace

Find empty UCLA classrooms to study in. The site shows every general assignment classroom, whether it's free right now (campus time), and its week of classes and club/org events.

## What's in here

- `index.html`: the whole frontend. It loads `classrooms.json` and does filtering, sorting and rendering in the browser.
- `classrooms.json`: the data the site reads.
- `scrape.py`: pulls class schedules and room characteristics from the UCLA Registrar.
- `scrape_hill.py`: pulls Hill study room availability from Residential Life into `hill.json`.
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

**The Hill.** A Campus / The Hill / Both switch at the top picks where to look. Two kinds of Hill rooms show up under The Hill:

- Registrar rooms in Hill buildings that have classes this term (Covel 210/218/225/319A, De Neve P350). `scrape.py` scrapes these along with the general assignment rooms. `--all` scrapes every registrar room.
- Residential study rooms (Hedrick, The Study at Hedrick, Rieber, Sproul, Olympic, Southwest Apartments, Gayley Heights) from [Residential Life reservations](https://reserve.reslife.ucla.edu/reserve). That site publicly lists every open hourly slot for the next two weeks, so `scrape_hill.py` writes `hill.json` with each room's open hours and free slots. Anything inside open hours that isn't listed is reserved. Only on-campus residents can book these rooms. Covel and Carnesale study spaces are not on that site.

Reservations change constantly, so the Pages workflow runs `scrape_hill.py` right before every deploy, every 30 minutes, instead of committing each change.

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
