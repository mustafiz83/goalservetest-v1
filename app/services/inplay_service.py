import requests
import gzip
import io
import time
import os
import json
import threading
import schedule
from datetime import datetime
from typing import Optional, Dict, Any
from app.core.config import settings
import re

# --- Configuration and Global State ---

# Scheduler Control Flags
STOP_SCHEDULER_FLAG = threading.Event()
scheduler_thread: Optional[threading.Thread] = None

# Job Configuration
INTERVAL_SECONDS = max(1, settings.INPLAY_SCHEDULER_INTERVAL_SECONDS)
LOOP_SLEEP_SECONDS = max(0.1, settings.SCHEDULER_LOOP_SLEEP_SECONDS)
INPLAY_SCHEDULER_JOB = settings.INPLAY_SCHEDULER_JOB # Set to False to disable the scheduler completely

# --- Core Data Fetching and Processing Functions ---

def fetch_and_decompress_data(api_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetches the response, attempts to decompress it (GZIP), and falls back to 
    parsing as plain JSON if decompression fails.
    """
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Requesting Goalserve content...")
    
    try:
        response = requests.get(api_url, timeout=settings.GOALSERVE_INPLAY_SCHEDULER_TIMEOUT_SECONDS)
        response.raise_for_status()

        # 1. ATTEMPT DECOMPRESSION (Original GZIP logic)
        try:
            compressed_file_stream = io.BytesIO(response.content)
            decompressed_string = gzip.GzipFile(
                fileobj=compressed_file_stream, 
                mode='rb'
            ).read().decode('utf-8')
            
            data = json.loads(decompressed_string)
            print("Status: SUCCESS. Data retrieved, decompressed (GZIP), and parsed.")
            return data

        except OSError as e:
            # 2. IF GZIP FAILS, ATTEMPT TO PARSE AS PLAIN JSON
            # This handles the reported error where the API returns uncompressed JSON.
            if "Not a gzipped file" in str(e) and response.content.startswith(b'{'):
                print("Status: GZIP DECOMPRESSION FAILED. Attempting to parse as plain JSON.")
                try:
                    # Treat the content as plain text JSON
                    data = response.json() 
                    print("Status: SUCCESS. Data retrieved and parsed (PLAIN JSON).")
                    return data
                except json.JSONDecodeError as json_e:
                    print(f"Status: FAILED TO PROCESS PLAIN JSON. Error: {json_e}")
                    return None
            else:
                # Handle other decompression errors
                print(f"Status: FAILED TO PROCESS GZ. Unhandled OSError: {e}")
                return None
        
        except json.JSONDecodeError as e:
            # Handle JSON decoding error after successful decompression attempt
            print(f"Status: FAILED TO PROCESS JSON. Error: {e}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"Status: CRITICAL NETWORK ERROR. Exception: {e}")
        return None

def process_api_response(data: Dict[str, Any]):
    """
    Processes the API response and saves snapshots to individual history files 
    for each event (events.{event_id}.json).
    """
    if not isinstance(data, dict) or 'events' not in data or not data['events']:
        print("Scheduler: Warning: 'events' key not found or is empty.")
        return

    processed_count = 0
    # Iterate through all event keys in the 'events' dictionary
    for event_id, event_data in data['events'].items():
        try:
            info = event_data.get('info')
            if not info: continue
            mid = info.get("mid")
            id = info.get("id")
            # if(mid != "126570764"):
            #     continue
            league_id = info.get("league_id")
            name = info.get("name")
            safe_name = re.sub(r'\W+', '_', name)
            # if(league_id != "70"):
            #     continue
            # 1. Create the new snapshot from the event's 'info' data
            snapshot = {
                # "timestamp": datetime.now().isoformat(),
                "minute": info.get("minute"),
                "seconds": info.get("seconds"),
                # "id": info.get("id"),
                # "name": info.get("name"),
                "ball_pos": info.get("ball_pos"),
                "state_info": info.get("state_info"),
                # "mid": info.get("mid"),
                # "state": info.get("state"),
                # "league_id": info.get("league_id"),
            }

            file_path = f"data/events.{league_id}.{id}.{mid}.{safe_name}.json"
            history = []

            # 2. Load existing history (if file exists)
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    file_content = f.read()
                    if file_content:
                        try:
                            history = json.loads(file_content)
                        except json.JSONDecodeError:
                            # Handle corrupted history file by starting fresh
                            print(f"Scheduler: Error decoding history file {file_path}. Starting new history.")

            # 3. Append the new snapshot
            history.append(snapshot)

            # 4. Write the updated history back to the file
            with open(file_path, 'w') as f:
                json.dump(history, f, indent=4)
                
            processed_count += 1

        except Exception as e:
            print(f"Scheduler Error processing event {event_id}: {e}")
            continue

    print(f"Scheduler: Successfully processed and saved data for {processed_count} events.")


def fetch_and_process_data():
    """
    The main job function executed by the scheduler.
    """
    data_content = fetch_and_decompress_data(settings.inplay_soccer_feed_url)

    if isinstance(data_content, dict):
        print("\n--- Starting Data Processing ---")
        process_api_response(data_content)
        print("--- Data Processing Complete ---\n")
    else:
        print("\nCRITICAL FAILURE: Job run skipped due to previous network/parsing error.\n")


# --- Thread Runner ---

def run_continuously():
    """
    The main scheduler loop that runs in a separate thread.
    It checks for pending jobs every second until the stop flag is set.
    """
    # Run the job immediately on startup
    fetch_and_process_data()
    
    while not STOP_SCHEDULER_FLAG.is_set():
        schedule.run_pending()
        time.sleep(LOOP_SLEEP_SECONDS) # Prevents high CPU usage
        
    print("Scheduler: Thread gracefully stopped.")

# --- Public API for FastAPI Integration ---

def start_scheduler():
    """
    Initializes and starts the background scheduler thread.
    """
    global scheduler_thread
    
    if(INPLAY_SCHEDULER_JOB == False):
        print("Scheduler: INPLAY_SCHEDULER_JOB is diabled")
        return
        
    print("Scheduler: INPLAY_SCHEDULER_JOB is enabled")
    
    if scheduler_thread is None or not scheduler_thread.is_alive():
        print(f"FastAPI Startup: Starting scheduler thread to run every {INTERVAL_SECONDS} seconds...")
        
        # Reset the stop flag in case it was previously set
        STOP_SCHEDULER_FLAG.clear() 
        
        # Configure the schedule library
        schedule.every(INTERVAL_SECONDS).seconds.do(fetch_and_process_data)
        
        # Initialize and start the thread
        scheduler_thread = threading.Thread(target=run_continuously, daemon=True)
        scheduler_thread.start()
        print("FastAPI Startup: Background scheduler thread started.")
    else:
        print("FastAPI Startup: Scheduler thread is already running.")


def stop_scheduler():
    """
    Gracefully stops the background scheduler thread.
    """
    global scheduler_thread
    if scheduler_thread and scheduler_thread.is_alive():
        print("FastAPI Shutdown: Setting stop flag for scheduler thread...")
        STOP_SCHEDULER_FLAG.set()
        # Give the thread a moment to shut down gracefully
        scheduler_thread.join(timeout=5) 
        if scheduler_thread.is_alive():
             print("Warning: Scheduler thread did not stop gracefully within 5 seconds.")
        else:
             print("FastAPI Shutdown: Scheduler thread stopped.")
        scheduler_thread = None
    else:
        print("FastAPI Shutdown: Scheduler thread was not active.")