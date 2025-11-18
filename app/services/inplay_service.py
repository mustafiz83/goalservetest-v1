import json
import os
import gzip
import requests
import time
import schedule
import threading
from typing import Dict, Any


# --- Configuration ---
API_ENDPOINT = "http://inplay.goalserve.com/inplay-soccer.gz"
INTERVAL_SECONDS = 5 # Schedule the fetch every 30 seconds

# Global flag to control the scheduler thread loop (used for graceful shutdown)
STOP_SCHEDULER_FLAG = threading.Event()
scheduler_thread: threading.Thread | None = None

# --- Core Logic ---

def process_api_response(data: Dict[str, Any]):
    """
    Processes the API response and saves snapshots to file.
    Note: For a production app, you might replace this file-based saving 
    with a call to an asynchronous database connector (e.g., PostgreSQL, MongoDB).
    """
    if not isinstance(data, dict) or 'events' not in data or not data['events']:
        print("Scheduler: Warning: 'events' key not found or is empty.")
        return

    # Iterate through all event keys in the 'events' dictionary
    for event_id, event_data in data['events'].items():
        try:
            info = event_data.get('info')
            if not info: continue
            
            snapshot = {
                "minute": info.get("minute"),
                "seconds": info.get("seconds"),
                "id": info.get("id"),
                "name": info.get("name"),
                "ball_pos": info.get("ball_pos"),
                "state_info": info.get("state_info") 
            }

            file_path = f"events.{event_id}.json"
            history = []

            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    file_content = f.read()
                    if file_content:
                        history = json.loads(file_content)

            history.append(snapshot)

            # Write the updated history back to the file
            with open(file_path, 'w') as f:
                json.dump(history, f, indent=4)

        except (json.JSONDecodeError, Exception) as e:
            # Log errors without interrupting the scheduler
            print(f"Scheduler Error processing event {event_id}: {e}")
            continue

    print(f"Scheduler: Successfully processed and saved data for {len(data['events'])} events.")


def fetch_and_process_data():
    """
    Handles the HTTP request, Gzip decompression, JSON decoding, and processing.
    """
    print(f"Scheduler: --- Fetching data at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    try:
        response = requests.get(API_ENDPOINT, stream=True, timeout=15)
        response.raise_for_status() 

        decompressed_data = gzip.decompress(response.content)
        json_string = decompressed_data.decode('utf-8')
        api_data = json.loads(json_string)
        
        process_api_response(api_data)

    except requests.exceptions.RequestException as e:
        print(f"Scheduler Error (Request): Could not fetch data. {e}")
    except (gzip.BadGzipFile, json.JSONDecodeError, Exception) as e:
        print(f"Scheduler Error (Data Processing): {type(e).__name__}: {e}")

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
        time.sleep(1) # Prevents high CPU usage

# --- Public API for FastAPI Integration ---

def start_scheduler():
    """
    Initializes and starts the background scheduler thread.
    """
    global scheduler_thread
    if scheduler_thread is None or not scheduler_thread.is_alive():
        print(f"FastAPI Startup: Starting scheduler thread to run every {INTERVAL_SECONDS} seconds...")
        # Configure the schedule library
        schedule.every(INTERVAL_SECONDS).seconds.do(fetch_and_process_data)
        
        # Initialize and start the thread
        scheduler_thread = threading.Thread(target=run_continuously, daemon=True)
        scheduler_thread.start()
        print("FastAPI Startup: Background scheduler thread started.")


def stop_scheduler():
    """
    Sets the flag to gracefully stop the background scheduler thread.
    """
    global scheduler_thread
    if scheduler_thread and scheduler_thread.is_alive():
        print("FastAPI Shutdown: Setting stop flag for scheduler thread...")
        # Signal the thread to stop its loop
        STOP_SCHEDULER_FLAG.set()
        # Wait for the thread to finish cleanly
        scheduler_thread.join(timeout=5) # Wait up to 5 seconds
        if scheduler_thread.is_alive():
            print("FastAPI Shutdown: Warning: Thread did not terminate gracefully.")
        else:
            print("FastAPI Shutdown: Background scheduler thread terminated gracefully.")