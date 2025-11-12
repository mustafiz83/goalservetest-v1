import httpx
import os
from typing import Dict, List, Any
# from datetime import datetime # Kept for completeness, though still unused

GOALSERVE_API_KEY = os.getenv("GOALSERVE_API_KEY", "your_api_key_here")
BASE_URL = "http://www.goalserve.com/getfeed"

class FootballLiveService:
    """Service for fetching and processing live football match data"""
    
    @staticmethod
    async def get_live_matches() -> Dict[str, Any]:
        """Fetch all live football matches"""
        try:
            async with httpx.AsyncClient() as client:
                url = f"{BASE_URL}/{GOALSERVE_API_KEY}/soccernew/live?json=1"
                print(url)
                response = await client.get(url, timeout=15.0)
                response.raise_for_status()
                
                data = response.json()
                matches = FootballLiveService._parse_live_matches(data)
                print(f"Total Matches Parsed: {len(matches)}")
                return {
                    "status": "success",
                    # Safely access the updated timestamp
                    "updated": data.get("scores", {}).get("@updated"),
                    "matches": matches
                }
        except Exception as e:
            # Catch HTTPX errors, JSON decoding errors, etc.
            return {
                "status": "error",
                "message": str(e),
                "matches": []
            }
    
    @staticmethod
    def _parse_live_matches(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse live match data from API response, handling single-object or list structures."""
        matches = []
        
        try:
            scores = data.get("scores", {})
            categories = scores.get("category", [])
            
            # Ensure categories is a list for iteration, even if it's a single dict or None
            if not isinstance(categories, list):
                categories = [categories] if categories else []
            
            for category in categories:
                # Handle cases where 'matches' might be missing
                matches_data = category.get("matches", {})
                
                # 'match' can be a single dict, a list of dicts, or None
                match_list = matches_data.get("match", [])
                
                # Ensure match_list is a list for iteration
                if not isinstance(match_list, list):
                    match_list = [match_list] if match_list else []
                
                for match in match_list:
                    # FIX: Ensure 'match' is a dictionary before processing (handles XML-to-JSON quirks)
                    if isinstance(match, dict) and match:
                        processed_match = FootballLiveService._process_match(category, match)
                        matches.append(processed_match)
                    print("processed")
        
        except Exception as e:
            # Print is for debugging, a more robust system might use a logger
          
            print(f"Error parsing live matches: {e}")
        
        return matches
    
    @staticmethod
    def _process_match(category: Dict[str, Any], match: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process and format individual match data. 
        Safely uses .get() with default {} to prevent 'NoneType' errors.
        """
        
        local_team = match.get("localteam") or {}
        visitor_team = match.get("visitorteam") or {}
        
        # FIX: Ensure live_stats is a dict, defaulting to {} if missing/None
        live_stats = match.get("live_stats") or {}
        
        # Safely extract and parse stats string
        stats_value = live_stats.get("@value", "") if isinstance(live_stats, dict) else ""
        parsed_stats = FootballLiveService._parse_live_stats(stats_value)
        
        # FIX: Ensure events_data is a dict, defaulting to {} if missing/None
        events_data = match.get("events") or {}
        events = events_data.get("event") or {}
        
        # Ensure events is a list for iteration, even if it's a single dict or None
        if not isinstance(events, list):
            events = [events] if events else []
        
        # FIX: Ensure halftime_data is a dict, defaulting to {} if missing/None
        halftime_data = match.get("ht") or {}
        
        # Prepare processed events list
        processed_events = [
            {
                "type": event.get("@type"),
                "minute": event.get("@minute"),
                "extra_min": event.get("@extra_min"),
                "team": "home" if event.get("@team") == "localteam" else "away",
                "player": event.get("@player"),
                "result": event.get("@result"),
                "player_id": event.get("@playerId"),
                "assist": event.get("@assist"),
                "assist_id": event.get("@assistid"),
                "timestamp": event.get("@ts")
            }
            # FIX: Ensure event is a dictionary before accessing its keys
            for event in events if isinstance(event, dict) 
        ]
        
        return {
            "match_id": match.get("@id"),
            "static_id": match.get("@static_id"),
            "fix_id": match.get("@fix_id"),
            "status": match.get("@status"),
            "timer": match.get("@timer"),
            "date": match.get("@formatted_date"),
            "time": match.get("@time"),
            "league": {
                "name": category.get("@name"),
                "id": category.get("@id"),
                "file_group": category.get("@file_group"),
                "is_cup": category.get("@iscup") == "True"
            },
            "home_team": {
                "name": local_team.get("@name"),
                "id": local_team.get("@id"),
                # Safely convert to int, defaulting to 0
                "goals": int(local_team.get("@goals") or 0)
            },
            "away_team": {
                "name": visitor_team.get("@name"),
                "id": visitor_team.get("@id"),
                # Safely convert to int, defaulting to 0
                "goals": int(visitor_team.get("@goals") or 0)
            },
            "halftime_score": halftime_data.get("@score"),
            "stats": {
                "corners": parsed_stats.get("corner") or {},
                "yellow_cards": parsed_stats.get("yellowcard") or {},
                "red_cards": parsed_stats.get("redcard") or {},
                "possession": parsed_stats.get("posession") or {},
                "attacks": parsed_stats.get("attacks") or {},
                "dangerous_attacks": parsed_stats.get("dangerousattacks") or {},
                "shots_on_target": parsed_stats.get("ontarget") or {},
                "shots_off_target": parsed_stats.get("offtarget") or {},
                "throw_ins": parsed_stats.get("throwin") or {},
                "free_kicks": parsed_stats.get("freekick") or {},
                "goal_kicks": parsed_stats.get("goalkick") or {},
                "penalties": parsed_stats.get("penalty") or {},
                "substitutions": parsed_stats.get("substitution") or {}
            },
            "events": processed_events
        }
    
    @staticmethod
    def _parse_live_stats(stats_string: str) -> Dict[str, Any]:
        """
        Parse pipe-separated live stats into dictionary (e.g., ICorner=home:5,away:6).
        This version specifically handles the complex 'home:X,away:Y' value structure.
        """
        stats: Dict[str, Any] = {}
        if not stats_string:
            return stats
        
        pairs = stats_string.split("|")
        
        for pair in pairs:
            if "=" in pair:
                try:
                    # Split the key=value pair (e.g., "ICorner=home:5,away:6")
                    key, value = pair.split("=", 1)
                except ValueError:
                    continue 

                # Remove 'I' prefix and convert to lowercase
                key = key.replace("I", "").lower()
                
                # --- NEW ROBUST PARSING LOGIC ---
                
                # We expect the value to contain two numeric parts separated by ',', ':', or both.
                
                # Try to find the numeric values directly.
                home_str = ""
                away_str = ""
                
                # Example: value = "home:5,away:6"
                if "home:" in value and "away:" in value:
                    # 1. Split on the comma to separate the home and away groups
                    home_group, away_group = value.split(",", 1)
                    
                    # 2. Extract the number after the colon in each group
                    # For "home:5", home_str becomes "5"
                    if ":" in home_group:
                        home_str = home_group.split(":", 1)[-1].strip()
                    
                    # For "away:6", away_str becomes "6"
                    if ":" in away_group:
                        away_str = away_group.split(":", 1)[-1].strip()

                elif ":" in value:
                    # Fallback for simpler 'X:Y' or complex 'X:Y:Z' formats (using maxsplit=1 safety)
                    parts = value.split(":", 1)
                    if len(parts) == 2:
                        home_str = parts[0]
                        away_str = parts[1].split(",", 1)[0] # Grab value before comma if it exists
                    elif len(parts) == 1:
                        # Handle cases like IPosession=home:,away:} where no colon is used
                        pass
                
                # Convert to int, treating empty strings or non-digits as 0
                try:
                    home = int(home_str) if home_str and home_str.isdigit() else 0
                    away = int(away_str) if away_str and away_str.isdigit() else 0
                    
                    stats[key] = {"home": home, "away": away}
                except Exception:
                    # Fallback for unexpected format
                    stats[key] = {"home": 0, "away": 0}
            
            # --- END NEW ROBUST PARSING LOGIC ---
            
        return stats