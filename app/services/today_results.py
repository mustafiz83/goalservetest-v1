import httpx
import os
import re
from typing import Dict, List, Any, Optional

GOALSERVE_API_KEY = os.getenv("GOALSERVE_API_KEY", "your_api_key_here")
BASE_URL = "http://www.goalserve.com/getfeed"

class TodayResultService:
    """Service for fetching and processing live football match data"""
    
    @staticmethod
    def _safe_int_attribute(value: Optional[str], default: int = 0) -> int:
        """
        Safely converts a string attribute (like goals) to an integer.
        Handles None, empty string, and the Goalserve placeholder '?' gracefully.
        """
        if value is None or value in ('', '?'):
            return default
        try:
            return int(value)
        except ValueError:
            return default

    @staticmethod
    async def get_live_matches() -> Dict[str, Any]:
        """Fetch all live football matches (or matches scheduled for today/tomorrow)"""
        print("Today's match result")
        try:
            async with httpx.AsyncClient() as client:
                # Using 'home' endpoint to get all matches for the day, including scheduled ones.
                url = f"{BASE_URL}/{GOALSERVE_API_KEY}/soccernew/home?json=1"
                print(url)
                response = await client.get(url, timeout=15.0)
                response.raise_for_status()
                
                data = response.json()
                matches = TodayResultService._parse_live_matches(data)
                print(f"Total Matches Parsed: {len(matches)}")
                return {
                    "status": "success",
                    # Safely access the updated timestamp
                    "updated": data.get("scores", {}).get("@updated"),
                    "matches": matches
                }
        except Exception as e:
            # Catch HTTPX errors, JSON decoding errors, etc.
            print(f"Error fetching live matches: {e}")
            return {
                "status": "error",
                "message": str(e),
                "matches": []
            }
    
    @staticmethod
    def _parse_live_matches(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse match data, handling Goalserve's inconsistent single-object or list structures."""
        matches = []
        
        try:
            scores = data.get("scores", {})
            categories = scores.get("category", [])
            
            # Ensure categories is a list for iteration
            if not isinstance(categories, list):
                categories = [categories] if categories else []
            
            for category in categories:
                if not isinstance(category, dict): continue

                matches_data = category.get("matches", {})
                if not isinstance(matches_data, dict): continue
                
                match_list = matches_data.get("match", [])
                
                # Ensure match_list is a list for iteration
                if not isinstance(match_list, list):
                    match_list = [match_list] if match_list else []
                
                for match in match_list:
                    # Ensure 'match' is a dictionary before processing
                    if isinstance(match, dict) and match:
                        try:
                            processed_match = TodayResultService._process_match(category, match)
                            matches.append(processed_match)
                        except Exception as e:
                            match_id = match.get("@id", "N/A")
                            print(f"Skipping match ID {match_id} due to processing error: {e}")
        
        except Exception as e:
            print(f"CRITICAL Error during main category parsing loop: {e}")
            
        return matches
    
    @staticmethod
    def _process_match(category: Dict[str, Any], match: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process and format individual match data with maximum safety checks.
        Safely uses .get() with default {} to prevent 'NoneType' errors.
        """
        
        # Safely retrieve top-level structures, defaulting to {}
        local_team = match.get("localteam") or {}
        visitor_team = match.get("visitorteam") or {}
        live_stats = match.get("live_stats") or {}
        events_data = match.get("events") or {}
        halftime_data = match.get("ht") or {}
        
        # Safely extract and parse stats string
        stats_value = ""
        if isinstance(live_stats, dict):
            stats_value = live_stats.get("@value", "")
        parsed_stats = TodayResultService._parse_live_stats(stats_value)
        
        # Process events, ensuring we start with a list
        events = events_data.get("event", [])
        if not isinstance(events, list):
            # Check if events is a single dict, otherwise treat it as empty list
            events = [events] if isinstance(events, dict) else []
        
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
            # Ensure event is a dictionary before accessing its keys
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
                # Safely convert goals, handling '?'
                "goals": TodayResultService._safe_int_attribute(local_team.get("@goals"))
            },
            "away_team": {
                "name": visitor_team.get("@name"),
                "id": visitor_team.get("@id"),
                # Safely convert goals, handling '?'
                "goals": TodayResultService._safe_int_attribute(visitor_team.get("@goals"))
            },
            "halftime_score": halftime_data.get("@score"),
            "stats": {
                "corners": parsed_stats.get("corner", {}),
                "yellow_cards": parsed_stats.get("yellowcard", {}),
                "red_cards": parsed_stats.get("redcard", {}),
                "possession": parsed_stats.get("posession", {}),
                "attacks": parsed_stats.get("attacks", {}),
                "dangerous_attacks": parsed_stats.get("dangerousattacks", {}),
                "shots_on_target": parsed_stats.get("ontarget", {}),
                "shots_off_target": parsed_stats.get("offtarget", {}),
                "throw_ins": parsed_stats.get("throwin", {}),
                "free_kicks": parsed_stats.get("freekick", {}),
                "goal_kicks": parsed_stats.get("goalkick", {}),
                "penalties": parsed_stats.get("penalty", {}),
                "substitutions": parsed_stats.get("substitution", {})
            },
            "events": processed_events
        }
    
    @staticmethod
    def _parse_live_stats(stats_string: str) -> Dict[str, Any]:
        """
        Parse pipe-separated live stats, including cleanup of Goalserve's trailing junk data.
        """
        stats: Dict[str, Any] = {}
        if not stats_string:
            return stats
        
        # --- FIX: Cleanup Trailing Junk ---
        # Find the index of the first character of the junk data: ',16:{'
        junk_start_index = stats_string.find(',16:{')
        
        if junk_start_index != -1:
            # If junk is found, trim the string immediately before the comma
            stats_string = stats_string[:junk_start_index]

        # Final safety cleanup for stray trailing characters like '|' or '}'
        stats_string = stats_string.rstrip('|').rstrip('}')
        
        # --- End Cleanup ---

        pairs = stats_string.split("|")
        
        # Regex to find the home and away numbers in the complex stat string
        regex_pattern = re.compile(r'home:(\d+).*away:(\d+)')
        
        for pair in pairs:
            if "=" in pair:
                try:
                    key, value = pair.split("=", 1)
                except ValueError:
                    continue 

                key = key.replace("I", "").lower()
                
                home = 0
                away = 0

                # Check if the value matches the complex 'home:X,away:Y' format
                match = regex_pattern.search(value)
                
                if match:
                    home_str = match.group(1)
                    away_str = match.group(2)
                    
                    home = int(home_str)
                    away = int(away_str)
                    
                    stats[key] = {"home": home, "away": away}
                else:
                    # Fallback for simpler formats (e.g., 'X:Y' or just a raw value)
                    parts = value.split(":", 1)
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        # If it's simple 'X:Y' and both are numbers
                        stats[key] = {"home": int(parts[0]), "away": int(parts[1])}
                    else:
                        stats[key] = value
        
        return stats