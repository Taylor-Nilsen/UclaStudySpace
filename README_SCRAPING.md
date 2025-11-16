# UCLA Classroom Schedule Scraper

A high-performance web scraper that collects classroom schedules from UCLA's registrar website using Selenium and multiprocessing.

## Overview

This project consists of two main scripts:
1. **`generate_urls.py`** - Generates classroom URLs and marks which rooms are offered
2. **`scrape.py`** - Scrapes schedule data from offered classrooms in parallel

## How It Works

### 1. URL Generation (`generate_urls.py`)

This script processes UCLA classroom data and generates properly formatted URLs for each classroom:

- Takes raw classroom data (building codes and room numbers)
- Matches against a list of "offered" classrooms (those actually available for scheduling)
- Generates encoded URLs for the UCLA registrar classroom detail pages
- Marks each classroom with `"offered": true` or `"offered": false`
- Outputs to `classrooms.json`

**What gets saved:**
```json
{
  "text": "BOELTER  2444",
  "building": "BOELTER",
  "room": "02444",
  "offered": true,
  "capacity": 80,
  "type": "Classroom",
  "url": "https://sa.ucla.edu/ro/Public/SOC/Results/ClassroomDetail?term=25F&classroom=BOELTER+%7C++02444++"
}
```

### 2. Schedule Scraping (`scrape.py`)

This script uses Selenium to scrape class schedules from each offered classroom:

**Process:**
1. Loads `classrooms.json` and filters for `"offered": true` classrooms
2. Creates multiple headless Chrome browser instances (parallel processing)
3. For each classroom:
   - Navigates to the UCLA classroom detail page
   - Waits for JavaScript calendar to load
   - Extracts schedule data from the page's JSON
   - Parses course information (name, type, times, enrollment)
   - Organizes events by day of week
4. Saves progress after each batch to prevent data loss

**What gets scraped:**
```json
"schedule": {
  "Monday": [
    {
      "course": "COM SCI 31",
      "type": "Lecture",
      "start_time": "10:00 AM",
      "end_time": "11:50 AM",
      "enrolled": 245,
      "capacity": 250
    }
  ],
  "Tuesday": [...],
  ...
}
```

## Setup

### Prerequisites

```bash
# Install required packages
pip install selenium beautifulsoup4

# Install ChromeDriver (macOS with Homebrew)
brew install chromedriver

# Or download manually from: https://chromedriver.chromium.org/
```

### Configuration

No configuration needed - the scripts work out of the box with the included classroom data.

## Usage

### Step 1: Generate URLs (if needed)

```bash
python generate_urls.py
```

This creates/updates `classrooms.json` with all classroom URLs and offered status.

### Step 2: Scrape Schedules

**Basic usage:**
```bash
# Scrape all offered classrooms with default settings (4 processes)
python scrape.py

# Scrape all offered classrooms with 12 parallel processes (faster)
python scrape.py 0 12

# Scrape first 10 offered classrooms with 8 processes
python scrape.py 10 8

# Scrape all with 12 processes and custom batch size of 24
python scrape.py 0 12 24
```

**Arguments:**
- `limit` - Number of classrooms to scrape (0 = all offered classrooms)
- `num_processes` - Number of parallel browser instances (default: 4)
- `batch_size` - Classrooms per batch before saving (default: same as num_processes)

**Recommended for speed:**
```bash
python scrape.py 0 12
```

## Performance

### Optimizations

- **Parallel Processing**: Multiple Chrome instances run simultaneously
- **Reduced Wait Times**: Optimized page load waits (6s max for initial load, 2s for AJAX)
- **Smart Filtering**: Only scrapes classrooms marked as "offered"
- **Batch Saving**: Saves progress after each batch to prevent data loss
- **Headless Browsers**: No GUI overhead

### Timing

- ~5-8 seconds per classroom (including waits)
- With 12 processes: ~150 classrooms in ~10 minutes
- Single process: ~150 classrooms in ~2 hours

## Output

All data is saved to `classrooms.json`. Each classroom entry includes:

```json
{
  "text": "BOELTER  2444",
  "building": "BOELTER",
  "room": "02444",
  "offered": true,
  "capacity": 80,
  "type": "Classroom",
  "url": "https://...",
  "schedule": {
    "Monday": [...],
    "Tuesday": [...],
    ...
  },
  "no_calendar": false
}
```

**Fields:**
- `schedule` - Dictionary of events organized by day of week
- `no_calendar` - Boolean indicating if the classroom has no scheduled classes
- `offered` - Boolean indicating if the classroom is available for scheduling

## Error Handling

### Automatic Recovery

- Progress is saved after each batch
- If the script crashes, restart it - already scraped data is preserved
- Failed classrooms are marked with `"no_calendar": null`

### Common Issues

**ChromeDriver not found:**
```bash
brew install chromedriver
# Or add ChromeDriver to your PATH
```

**Rate limiting:**
- Reduce `num_processes` if you get connection errors
- UCLA's servers are generally tolerant of parallel requests

**Memory issues:**
- Reduce `num_processes` (try 4-6 instead of 12)
- Reduce `batch_size` to save more frequently

## Architecture

```
generate_urls.py
├── Reads classroom data
├── Matches with offered rooms
└── Outputs classrooms.json

scrape.py
├── Loads classrooms.json
├── Filters for offered=true
├── Multiprocessing Pool
│   ├── Worker 1: Chrome + Selenium
│   ├── Worker 2: Chrome + Selenium
│   └── Worker N: Chrome + Selenium
├── Batch processing
│   ├── Scrape batch
│   └── Save to classrooms.json
└── Final save and statistics
```

## Development

### Key Functions

**`generate_urls.py`:**
- `normalize_room_number()` - Handles different room number formats
- `generate_urls()` - Creates URLs and matches offered rooms

**`scrape.py`:**
- `scrape_classroom_schedule()` - Scrapes one classroom
- `process_classroom_worker()` - Worker function for parallel processing
- `main()` - Orchestrates the scraping process

### Modifying Wait Times

Edit these values in `scrape_classroom_schedule()`:
```python
WebDriverWait(driver, 6)  # Wait for page load
time.sleep(2)             # Wait for AJAX
WebDriverWait(driver, 4)  # Wait for calendar
```

## License

This tool is for educational purposes. Please respect UCLA's servers and use reasonable rate limiting.
