import json
import requests
from bs4 import BeautifulSoup
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import time
from multiprocessing import Pool, Manager, cpu_count
import os

def scrape_classroom_schedule(url, driver):
    """
    Scrape the classroom schedule from a UCLA classroom detail page using Selenium.
    Returns a dictionary with the schedule organized by day of week.
    
    Args:
        url: The URL to scrape
        driver: An existing Selenium WebDriver instance to reuse
    """
    try:
        driver.get(url)
        
        # Optimized waits - reduced significantly
        try:
            wait = WebDriverWait(driver, 8)  # Reduced from 15
            wait.until(EC.presence_of_element_located((By.ID, "classroomDetails")))
        except:
            pass  # Continue anyway
        
        # Reduced AJAX wait from 5 to 3 seconds
        time.sleep(3)
        
        # Try to wait for calendar events with shorter timeout
        try:
            wait = WebDriverWait(driver, 5)  # Reduced from 10
            wait.until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, ".fc-event, [class*='fc-event']")) > 0 or
                                     len(driver.find_elements(By.CSS_SELECTOR, "[data-time], [class*='calendar']")) > 0)
        except:
            pass  # Continue anyway
        
        # Get the page source after JavaScript has executed
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        
        # Find the script that contains createFullCalendar with JSON data
        calendar_data = None
        
        for script in soup.find_all('script'):
            script_text = script.string
            if script_text and 'createFullCalendar' in script_text:
                # Extract the JSON array from the script
                # The pattern is: createFullCalendar($.parseJSON('...'))
                match = re.search(r'createFullCalendar\(\$\.parseJSON\(\'(.+?)\'\)\)', script_text)
                if match:
                    json_str = match.group(1)
                    # Unescape the JSON string
                    json_str = json_str.replace('\\"', '"')
                    try:
                        calendar_data = json.loads(json_str)
                        if len(calendar_data) == 0:
                            return {"no_calendar": True, "schedule": {}}
                        break
                    except json.JSONDecodeError as e:
                        pass
        
        # Check if there's a message indicating no calendar data
        if not calendar_data:
            # Look for "No classes are scheduled" or similar messages
            calendar_div = soup.find('div', id='calendar')
            if calendar_div:
                text_content = calendar_div.get_text(strip=True)
                if not text_content or 'no classes' in text_content.lower():
                    return {"no_calendar": True, "schedule": {}}
            
            return {"no_calendar": True, "schedule": {}}
        
        # Process the JSON data into our schedule format
        # Days of the week
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        schedule = {day: [] for day in days}
        
        # Parse each event
        for event in calendar_data:
            # Extract date/time information
            start_dt_str = event.get('start', '')
            end_dt_str = event.get('end', '')
            
            if start_dt_str:
                # Parse the date to get day of week
                from datetime import datetime
                try:
                    # Handle both full datetime strings and time-only strings
                    if 'T' in start_dt_str:
                        start_dt = datetime.fromisoformat(start_dt_str)
                        end_dt = datetime.fromisoformat(end_dt_str) if end_dt_str and 'T' in end_dt_str else None
                    else:
                        # If it's just a time string, we can't determine the day
                        # Try to get it from the Days_in_week field
                        days_str = event.get('Days_in_week', '').strip()
                        strt_time = event.get('strt_time', start_dt_str)
                        stop_time = event.get('stop_time', end_dt_str)
                        
                        # Parse time strings
                        if strt_time:
                            start_dt = datetime.strptime(strt_time, '%H:%M:%S')
                        else:
                            continue
                        
                        if stop_time:
                            end_dt = datetime.strptime(stop_time, '%H:%M:%S')
                        else:
                            end_dt = None
                        
                        # Map day codes to day names
                        day_map = {'M': 'Monday', 'T': 'Tuesday', 'W': 'Wednesday', 'R': 'Thursday', 'F': 'Friday', 'S': 'Saturday', 'U': 'Sunday'}
                        
                        # Process each day this class meets
                        for day_code in days_str:
                            day_of_week = day_map.get(day_code)
                            if not day_of_week:
                                continue
                            
                            start_time = start_dt.strftime('%I:%M %p')
                            end_time = end_dt.strftime('%I:%M %p') if end_dt else ''
                            
                            # Extract course information
                            course_name = event.get('title', '').strip()
                            course_type = event.get('lecture', '').strip()
                            enrollment_str = event.get('enrollment', '')
                            
                            # Parse enrollment
                            enr_match = re.search(r'Enr:\s*(\d+)\s*of\s*(\d+)', enrollment_str)
                            if enr_match:
                                enrolled = int(enr_match.group(1))
                                capacity = int(enr_match.group(2))
                            else:
                                enrolled = event.get('enroll_total')
                                capacity = event.get('enroll_capacity')
                            
                            event_data = {
                                'course': course_name,
                                'type': course_type,
                                'start_time': start_time,
                                'end_time': end_time,
                                'enrolled': enrolled,
                                'capacity': capacity
                            }
                            
                            if day_of_week in schedule:
                                schedule[day_of_week].append(event_data)
                        
                        continue  # Skip to next event
                    
                    day_of_week = start_dt.strftime('%A')
                    start_time = start_dt.strftime('%I:%M %p')
                    end_time = end_dt.strftime('%I:%M %p') if end_dt else ''
                    
                    # Extract course information
                    course_name = event.get('title', '').strip()
                    course_type = event.get('lecture', '').strip()
                    enrollment_str = event.get('enrollment', '')
                    
                    # Parse enrollment
                    enr_match = re.search(r'Enr:\s*(\d+)\s*of\s*(\d+)', enrollment_str)
                    if enr_match:
                        enrolled = int(enr_match.group(1))
                        capacity = int(enr_match.group(2))
                    else:
                        enrolled = event.get('enroll_total')
                        capacity = event.get('enroll_capacity')
                    
                    event_data = {
                        'course': course_name,
                        'type': course_type,
                        'start_time': start_time,
                        'end_time': end_time,
                        'enrolled': enrolled,
                        'capacity': capacity
                    }
                    
                    if day_of_week in schedule:
                        schedule[day_of_week].append(event_data)
                        
                except Exception as e:
                    continue
        
        # Sort events by time for each day
        for day in schedule:
            schedule[day].sort(key=lambda x: x['start_time'])
        
        return {"no_calendar": False, "schedule": schedule}
        
    except Exception as e:
        return None


def process_classroom_worker(args):
    """
    Worker function for multiprocessing. Each worker creates its own browser instance.
    
    Args:
        args: Tuple of (classroom_dict, index, total)
    
    Returns:
        Tuple of (index, classroom_dict_with_schedule, stats)
    """
    classroom, index, total = args
    driver = None
    
    try:
        # Set up Chrome options for headless browsing
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--disable-logging')
        chrome_options.add_argument('--log-level=3')
        
        # Initialize the Chrome driver
        driver = webdriver.Chrome(options=chrome_options)
        
        building = classroom.get('building', 'Unknown')
        room = classroom.get('room', 'Unknown')
        url = classroom.get('url', '')
        
        print(f"[{index}/{total}] Processing: {building} {room}")
        
        # Scrape the schedule
        result = scrape_classroom_schedule(url, driver)
        
        stats = {'success': 0, 'no_calendar': 0, 'failed': 0}
        
        if result:
            schedule = result.get('schedule', {})
            has_no_calendar = result.get('no_calendar', False)
            
            if has_no_calendar:
                classroom['schedule'] = None
                classroom['no_calendar'] = True
                stats['no_calendar'] = 1
                print(f"[{index}/{total}] ⚠️  {building} {room} - No calendar")
            else:
                classroom['schedule'] = schedule
                classroom['no_calendar'] = False
                total_events = sum(len(events) for events in schedule.values())
                stats['success'] = 1
                print(f"[{index}/{total}] ✓ {building} {room} - {total_events} events")
        else:
            classroom['schedule'] = None
            classroom['no_calendar'] = None
            stats['failed'] = 1
            print(f"[{index}/{total}] ❌ {building} {room} - Failed")
        
        return (index, classroom, stats)
        
    except Exception as e:
        print(f"[{index}/{total}] ❌ Error: {e}")
        classroom['schedule'] = None
        classroom['no_calendar'] = None
        return (index, classroom, {'success': 0, 'no_calendar': 0, 'failed': 1})
    finally:
        if driver:
            driver.quit()


def main(limit=None, workers=4):
    """Main function to scrape schedules from all classrooms using multiprocessing
    
    Args:
        limit: Optional integer to limit how many classrooms to scrape
        workers: Number of parallel workers (default: 4)
    """
    # Load the classrooms data
    print("Loading classrooms.json...")
    with open('classrooms.json', 'r') as f:
        all_classrooms = json.load(f)
    
    # Check if data is a list (top-level array)
    if not isinstance(all_classrooms, list):
        print("Error: classrooms.json should contain a top-level array")
        return
    
    if not all_classrooms:
        print("No classrooms found in classrooms.json")
        return
    
    # Determine which classrooms to scrape
    if limit and limit > 0:
        classrooms_to_scrape = all_classrooms[:limit]
        print(f"\nLimiting to first {limit} classroom(s) out of {len(all_classrooms)} total")
    else:
        classrooms_to_scrape = all_classrooms
    
    total_classrooms = len(classrooms_to_scrape)
    print(f"\nFound {total_classrooms} classroom(s) to scrape")
    print(f"Using {workers} parallel workers")
    print("="*80)
    
    # Track statistics
    successful_scrapes = 0
    no_calendar_count = 0
    failed_scrapes = 0
    
    # Prepare arguments for workers
    work_items = [(classroom, i+1, total_classrooms) for i, classroom in enumerate(classrooms_to_scrape)]
    
    # Process classrooms in parallel
    print(f"\n🚀 Starting parallel scraping with {workers} workers...\n")
    
    # Store results with their original indices
    results = []
    
    with Pool(processes=workers) as pool:
        # Process in chunks and save periodically
        chunk_size = 20  # Save every 20 completed items
        completed = 0
        
        for result in pool.imap_unordered(process_classroom_worker, work_items):
            index, classroom_data, stats = result
            results.append((index, classroom_data))
            
            # Update statistics
            successful_scrapes += stats['success']
            no_calendar_count += stats['no_calendar']
            failed_scrapes += stats['failed']
            
            completed += 1
            
            # Save progress periodically
            if completed % chunk_size == 0 or completed == total_classrooms:
                # Sort results by index and update classrooms
                results.sort(key=lambda x: x[0])
                for idx, classroom_data in results:
                    classrooms_to_scrape[idx - 1] = classroom_data
                
                print(f"\n{'='*80}")
                print(f"💾 Saving progress... ({completed}/{total_classrooms} complete)")
                with open('classrooms.json', 'w') as f:
                    json.dump(all_classrooms, f, indent=4)
                print("✓ Saved!")
                print(f"{'='*80}\n")
    
    # Final save with all results
    results.sort(key=lambda x: x[0])
    for idx, classroom_data in results:
        classrooms_to_scrape[idx - 1] = classroom_data
    
    print("\n" + "="*80)
    print("SCRAPING COMPLETE - Saving final data to classrooms.json...")
    print("="*80)
    with open('classrooms.json', 'w') as f:
        json.dump(all_classrooms, f, indent=4)
    print("✓ Data saved successfully!")
    
    # Print final statistics
    print("\n" + "="*80)
    print("SCRAPING STATISTICS:")
    print("="*80)
    print(f"Total classrooms processed: {total_classrooms}")
    print(f"Successfully scraped: {successful_scrapes}")
    print(f"No calendar available: {no_calendar_count}")
    print(f"Failed: {failed_scrapes}")
    print("="*80)


if __name__ == "__main__":
    # Allow optional command line arguments to specify how many to scrape and number of workers
    import sys
    
    limit = None
    workers = 4  # Default to 4 parallel workers
    
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
            print(f"Limiting scrape to first {limit} classrooms...")
        except ValueError:
            print("Invalid limit argument, processing all classrooms")
    
    if len(sys.argv) > 2:
        try:
            workers = int(sys.argv[2])
            print(f"Using {workers} parallel workers...")
        except ValueError:
            print("Invalid workers argument, using default (4)")
    
    main(limit, workers)
