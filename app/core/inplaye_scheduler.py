import time
import schedule
from app.services.inplay_service import fetch_and_process_data

# --- Configuration ---
# Set the interval for the scheduling
INTERVAL_SECONDS = 10 

def run_scraper_job():
    """
    Wrapper function to execute the main scraper logic.
    This is the function that the schedule library will call.
    """
    print(f"Scheduling job: Fetching data (next run in {INTERVAL_SECONDS} seconds)...")
    fetch_and_process_data()

# --- Setup the Schedule ---

# 1. Schedule the job to run every 30 seconds
schedule.every(INTERVAL_SECONDS).seconds.do(run_scraper_job)

# 2. Run the job immediately on startup to get the first data point
print("--- Starting Soccer Data Ingest Scheduler ---")
run_scraper_job() 

# --- Main Scheduling Loop ---

while True:
    # Check if a scheduled job is due to run
    schedule.run_pending()
    
    # Wait for 1 second before checking again. 
    # This prevents the loop from consuming too much CPU.
    time.sleep(1)

# Note: Since the loop is infinite (while True), this script runs until manually stopped (e.g., Ctrl+C).