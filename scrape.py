import json
from bs4 import BeautifulSoup
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import time
from multiprocessing import Pool
from datetime import datetime

def scrape_classroom_schedule(url, driver):
    """
    Scrape the classroom schedule from a UCLA classroom detail page using Selenium.
    Returns a dictionary with the schedule organized by day of week.
    """
    try:
        driver.get(url)
        
        try:
            WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.ID, "classroomDetails")))
        except:
            pass
        
        time.sleep(2)
        
        try:
            WebDriverWait(driver, 4).until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, ".fc-event, [class*='fc-event']")) > 0)
        except:
            pass
        
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        
        calendar_data = None
        
        for script in soup.find_all('script'):
            script_text = script.string
            if script_text and 'createFullCalendar' in script_text:
                match = re.search(r'createFullCalendar\(\$\.parseJSON\(\'(.+?)\'\)\)', script_text)
                if match:
                    json_str = match.group(1).replace('\\"', '"')
                    try:
                        calendar_data = json.loads(json_str)
                        if len(calendar_data) == 0:
                            return {"no_calendar": True, "schedule": {}}
                        break
                    except json.JSONDecodeError:
                        pass
        
        if not calendar_data:
            calendar_div = soup.find('div', id='calendar')
            if calendar_div and ('no classes' in calendar_div.get_text(strip=True).lower() or not calendar_div.get_text(strip=True)):
                return {"no_calendar": True, "schedule": {}}
            return {"no_calendar": True, "schedule": {}}
        
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        schedule = {day: [] for day in days}
        
        for event in calendar_data:
            start_dt_str = event.get('start', '')
            end_dt_str = event.get('end', '')
            
            if start_dt_str:
                try:
                    if 'T' in start_dt_str:
                        start_dt = datetime.fromisoformat(start_dt_str)
                        end_dt = datetime.fromisoformat(end_dt_str) if end_dt_str and 'T' in end_dt_str else None
                    else:
                        days_str = event.get('Days_in_week', '').strip()
                        strt_time = event.get('strt_time', start_dt_str)
                        stop_time = event.get('stop_time', end_dt_str)
                        
                        if strt_time:
                            start_dt = datetime.strptime(strt_time, '%H:%M:%S')
                        else:
                            continue
                        
                        end_dt = datetime.strptime(stop_time, '%H:%M:%S') if stop_time else None
                        
                        day_map = {'M': 'Monday', 'T': 'Tuesday', 'W': 'Wednesday', 'R': 'Thursday', 'F': 'Friday', 'S': 'Saturday', 'U': 'Sunday'}
                        
                        for day_code in days_str:
                            day_of_week = day_map.get(day_code)
                            if not day_of_week:
                                continue
                            
                            start_time = start_dt.strftime('%I:%M %p')
                            end_time = end_dt.strftime('%I:%M %p') if end_dt else ''
                            
                            course_name = event.get('title', '').strip()
                            course_type = event.get('lecture', '').strip()
                            enrollment_str = event.get('enrollment', '')
                            
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
                        
                        continue
                    
                    day_of_week = start_dt.strftime('%A')
                    start_time = start_dt.strftime('%I:%M %p')
                    end_time = end_dt.strftime('%I:%M %p') if end_dt else ''
                    
                    course_name = event.get('title', '').strip()
                    course_type = event.get('lecture', '').strip()
                    enrollment_str = event.get('enrollment', '')
                    
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
                        
                except Exception:
                    continue
        
        for day in schedule:
            schedule[day].sort(key=lambda x: x['start_time'])
        
        return {"no_calendar": False, "schedule": schedule}
        
    except Exception:
        return None


def process_classroom_worker(args):
    """Worker function for multiprocessing."""
    classroom, index, total = args
    driver = None
    
    try:
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--disable-logging')
        chrome_options.add_argument('--log-level=3')
        
        driver = webdriver.Chrome(options=chrome_options)
        
        building = classroom.get('building', 'Unknown')
        room = classroom.get('room', 'Unknown')
        url = classroom.get('url', '')
        
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
    """Main function to scrape schedules from all classrooms using multiprocessing"""
    with open('classrooms.json', 'r') as f:
        all_classrooms = json.load(f)
    
    if not isinstance(all_classrooms, list):
        print("ERROR: classrooms.json must contain a top-level array")
        return
    
    if not all_classrooms:
        print("ERROR: No classrooms found in classrooms.json")
        return
    
    # Filter for only offered classrooms
    classrooms_to_scrape = [c for c in all_classrooms if c.get('offered', False)]
    
    if limit and limit > 0:
        classrooms_to_scrape = classrooms_to_scrape[:limit]
    
    total_classrooms = len(classrooms_to_scrape)
    
    if batch_size is None:
        batch_size = num_processes
    
    print(f"Total: {total_classrooms} | Processes: {num_processes} | Batch size: {batch_size}")
    print("="*80)
    
    total_success = 0
    total_no_calendar = 0
    total_failed = 0
    
    work_items = [(classroom, i+1, total_classrooms) for i, classroom in enumerate(classrooms_to_scrape)]
    
    print(f"Starting parallel execution...\n")
    
    with Pool(processes=num_processes) as pool:
        for batch_start in range(0, total_classrooms, batch_size):
            batch_end = min(batch_start + batch_size, total_classrooms)
            batch_items = work_items[batch_start:batch_end]
            
            print(f"Batch [{batch_start + 1}-{batch_end}/{total_classrooms}]")
            
            batch_results = pool.map(process_classroom_worker, batch_items)
            
            for index, classroom_data, stats in batch_results:
                classrooms_to_scrape[index - 1] = classroom_data
                
                total_success += stats['success']
                total_no_calendar += stats['no_calendar']
                total_failed += stats['failed']
            
            with open('classrooms.json', 'w') as f:
                json.dump(all_classrooms, f, indent=4)
            
            print(f"Saved: {batch_end}/{total_classrooms} | Success: {total_success} | No calendar: {total_no_calendar} | Failed: {total_failed}\n")
    
    print("\n" + "="*80)
    print("COMPLETE")
    print("="*80)
    with open('classrooms.json', 'w') as f:
        json.dump(all_classrooms, f, indent=4)
    
    print(f"Total processed: {total_classrooms}")
    print(f"Success: {total_success}")
    print(f"No calendar: {total_no_calendar}")
    print(f"Failed: {total_failed}")
    print("="*80)


if __name__ == "__main__":
    import sys
    
    limit = None
    num_processes = 4
    batch_size = None
    
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
