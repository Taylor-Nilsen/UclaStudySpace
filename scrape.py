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
                print(f"[{index}/{total}] {building} {room}: NO_CALENDAR")
            else:
                classroom['schedule'] = schedule
                classroom['no_calendar'] = False
                total_events = sum(len(events) for events in schedule.values())
                stats['success'] = 1
                print(f"[{index}/{total}] {building} {room}: OK ({total_events} events)")
        else:
            classroom['schedule'] = None
            classroom['no_calendar'] = None
            stats['failed'] = 1
            print(f"[{index}/{total}] {building} {room}: FAILED")
        
        return (index, classroom, stats)
        
    except Exception as e:
        print(f"[{index}/{total}] ERROR: {e}")
        classroom['schedule'] = None
        classroom['no_calendar'] = None
        return (index, classroom, {'success': 0, 'no_calendar': 0, 'failed': 1})
    finally:
        if driver:
            driver.quit()


def main(limit=None, num_processes=4, batch_size=None):
    """Main function to scrape schedules from all classrooms using multiprocessing
    
    Args:
        limit: Optional integer to limit how many classrooms to scrape
        num_processes: Number of parallel processes (default: 4)
        batch_size: Number of items to process per batch (default: same as num_processes)
    """
    # Load the classrooms data
    with open('classrooms.json', 'r') as f:
        all_classrooms = json.load(f)
    
    # Check if data is a list (top-level array)
    if not isinstance(all_classrooms, list):
        print("ERROR: classrooms.json must contain a top-level array")
        return
    
    if not all_classrooms:
        print("ERROR: No classrooms found in classrooms.json")
        return
    
    # Determine which classrooms to scrape
    if limit and limit > 0:
        classrooms_to_scrape = all_classrooms[:limit]
    else:
        classrooms_to_scrape = all_classrooms
    
    total_classrooms = len(classrooms_to_scrape)
    
    # Set batch size (defaults to num_processes if not specified)
    if batch_size is None:
        batch_size = num_processes
    
    print(f"Total: {total_classrooms} | Processes: {num_processes} | Batch size: {batch_size}")
    print("="*80)
    
    # Track statistics
    total_success = 0
    total_no_calendar = 0
    total_failed = 0
    
    # Prepare arguments for workers
    work_items = [(classroom, i+1, total_classrooms) for i, classroom in enumerate(classrooms_to_scrape)]
    
    # Process classrooms in parallel
    print(f"Starting parallel execution...\n")
    
    # Process in batches
    # batch_size controls how many items are processed in parallel per batch
    with Pool(processes=num_processes) as pool:
        for batch_start in range(0, total_classrooms, batch_size):
            batch_end = min(batch_start + batch_size, total_classrooms)
            batch_items = work_items[batch_start:batch_end]
            
            print(f"Batch [{batch_start + 1}-{batch_end}/{total_classrooms}]")
            
            # Process this batch in parallel
            batch_results = pool.map(process_classroom_worker, batch_items)
            
            # Update the classrooms with results from this batch
            for index, classroom_data, stats in batch_results:
                classrooms_to_scrape[index - 1] = classroom_data
                
                # Update statistics
                total_success += stats['success']
                total_no_calendar += stats['no_calendar']
                total_failed += stats['failed']
            
            # Save after each batch completes
            with open('classrooms.json', 'w') as f:
                json.dump(all_classrooms, f, indent=4)
            
            print(f"Saved: {batch_end}/{total_classrooms} | Success: {total_success} | No calendar: {total_no_calendar} | Failed: {total_failed}\n")
    
    print("\n" + "="*80)
    print("COMPLETE")
    print("="*80)
    with open('classrooms.json', 'w') as f:
        json.dump(all_classrooms, f, indent=4)
    
    # Print final statistics
    print(f"Total processed: {total_classrooms}")
    print(f"Success: {total_success}")
    print(f"No calendar: {total_no_calendar}")
    print(f"Failed: {total_failed}")
    print("="*80)


if __name__ == "__main__":
    # Allow optional command line arguments
    import sys
    
    limit = None
    num_processes = 4  # Default to 4 parallel processes
    batch_size = None  # Default to same as num_processes
    
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except ValueError:
            print("ERROR: Invalid limit argument")
    
    if len(sys.argv) > 2:
        try:
            num_processes = int(sys.argv[2])
        except ValueError:
            print("ERROR: Invalid num_processes argument, using default (4)")
    
    if len(sys.argv) > 3:
        try:
            batch_size = int(sys.argv[3])
        except ValueError:
            print("ERROR: Invalid batch_size argument, using default (same as num_processes)")
    
    main(limit, num_processes, batch_size)
