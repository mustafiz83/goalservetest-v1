import json
import os
import io
import gzip
import requests
import time
import schedule
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from app.core.config import settings

# --- Configuration ---
API_ENDPOINT = "http://inplay.goalserve.com/inplay-soccer.gz"
INTERVAL_SECONDS = 5 # Schedule the fetch every 30 seconds

# Global flag to control the scheduler thread loop (used for graceful shutdown)
STOP_SCHEDULER_FLAG = threading.Event()
scheduler_thread: threading.Thread | None = None
INPLAY_SCHEDULER_JOB = settings.INPLAY_SCHEDULER_JOB

# --- Core Logic ---

def process_api_response(data: Dict[str, Any]):
    """
    Processes the API response and saves snapshots to individual history files 
    for each event.

    It assumes the 'data' dictionary has a structure like:
    {'events': {'event_id_1': {'info': {...}}, 'event_id_2': {'info': {...}}}}
    """
    if not isinstance(data, dict) or 'events' not in data or not data['events']:
        print("Scheduler: Warning: 'events' key not found or is empty.")
        return

    # Iterate through all event keys in the 'events' dictionary
    for event_id, event_data in data['events'].items():
        try:
            info = event_data.get('info')
            if not info: 
                print(f"Scheduler: Skipping event {event_id} due to missing 'info'.")
                continue
            
            # 1. Create the new snapshot from the event's 'info' data
            snapshot = {
                "timestamp": datetime.now().isoformat(), # Add current timestamp for tracking
                "minute": info.get("minute"),
                "seconds": info.get("seconds"),
                "id": info.get("id"),
                "name": info.get("name"),
                "ball_pos": info.get("ball_pos"),
                "state_info": info.get("state_info") 
            }

            file_path = f"events.{event_id}.json"
            history = []

            # 2. Load existing history (if file exists)
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    file_content = f.read()
                    if file_content:
                        try:
                            history = json.loads(file_content)
                        except json.JSONDecodeError:
                            print(f"Scheduler: Error decoding history file {file_path}. Starting new history.")

            # 3. Append the new snapshot
            history.append(snapshot)

            # 4. Write the updated history back to the file
            with open(file_path, 'w') as f:
                json.dump(history, f, indent=4)

        except Exception as e:
            # Log errors without interrupting the scheduler
            print(f"Scheduler Error processing event {event_id}: {e}")
            continue

    print(f"Scheduler: Successfully processed and saved data for {len(data['events'])} events.")

# --- Core Fetching Logic ---

def fetch_and_decompress_data() -> Optional[Dict[str, Any]]:
    """
    Fetches the raw binary response (.gz file), decompresses it, and parses 
    the result as JSON.
    """
    api_url = API_ENDPOINT
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Requesting Goalserve .gz file content...")
    
    try:
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()

        # --- Primary Path: Attempt to Decompress and Parse JSON ---
        try:
            compressed_file_stream = io.BytesIO(response.content)
            decompressed_string = gzip.GzipFile(
                fileobj=compressed_file_stream, 
                mode='rb'
            ).read().decode('utf-8')
            
            # Parse the decompressed string as JSON
            data = json.loads(decompressed_string)
            
            print("Status: SUCCESS. Data retrieved, decompressed, and parsed as JSON.")
            return data

        except OSError as e:
            # Secondary Path: Decompression Failure (likely a plain text API error)
            print(f"Status: FAILED TO DECOMPRESS (OSError: {e}).")
            error_message = response.text
            print(f"Goalserve Error Response (Plain Text): {error_message[:200]}...")
            return None # Treat API errors that aren't JSON as a failed run

        except json.JSONDecodeError as e:
            # Decompressed successfully, but content isn't valid JSON
            print(f"Status: FAILED TO PARSE JSON. {e}")
            return None

    except requests.exceptions.RequestException as e:
        # Handle network issues (DNS, timeouts) and unhandled HTTP errors.
        print(f"Status: CRITICAL NETWORK ERROR. An exception occurred: {e}")
        return None
    
    
# --- Thread Runner ---

def run_continuously():
    """
    The main scheduler loop that runs in a separate thread.
    It checks for pending jobs every second until the stop flag is set.
    """
    # Run the job immediately on startup
    fetch_and_decompress_data()
    
    while not STOP_SCHEDULER_FLAG.is_set():
        schedule.run_pending()
        time.sleep(1) # Prevents high CPU usage

# --- Public API for FastAPI Integration ---

def start_scheduler():
    """
    Initializes and starts the background scheduler thread.
    """
    if(INPLAY_SCHEDULER_JOB == False):
       print("Scheduler: INPLAY_SCHEDULER_JOB is diabled")
       return
    print("Scheduler: INPLAY_SCHEDULER_JOB is enabled")
    global scheduler_thread
    if scheduler_thread is None or not scheduler_thread.is_alive():
        print(f"FastAPI Startup: Starting scheduler thread to run every {INTERVAL_SECONDS} seconds...")
        # Configure the schedule library
        schedule.every(INTERVAL_SECONDS).seconds.do(fetch_and_decompress_data)
        
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