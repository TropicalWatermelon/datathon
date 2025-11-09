# utils/wger_client.py
import requests
from typing import Optional, List, Dict, Any
from functools import lru_cache

# The public wger API base URL
BASE_URL = "https://wger.de/api/v2"

# We assume '2' is English. This is the standard.
LANGUAGE_ID = 2

@lru_cache(maxsize=1)
def get_category_map() -> Dict[str, int]:
    """
    Fetches all exercise categories (e.g., Strength, Cardio)
    and maps their names to their IDs.
    
    This is called once and cached.
    """
    print("Fetching wger category map...")
    try:
        res = requests.get(f"{BASE_URL}/exercisecategory/")
        res.raise_for_status() # Raise an error on a bad response
        data = res.json()
        
        # Create a "translation map" from goal name to category ID
        # e.g., {"Strength": 10, "Cardio": 8, "Stretching": 14, ...}
        return {
            item['name'].lower(): item['id']
            for item in data.get('results', [])
        }
    except Exception as e:
        print(f"Error fetching wger categories: {e}")
        # Fallback to a hard-coded, known-good map
        return {
            "abs": 10,
            "arms": 8,
            "back": 12,
            "calves": 14,
            "chest": 11,
            "legs": 9,
            "shoulders": 13,
            "cardio": 15, 
            "strength": 10, 
            "conditioning": 15 
        }

def get_exercises_by_goal(goal_name: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Fetches a list of exercises based on a goal (e.g., "strength").
    """
    category_map = get_category_map()
    
    # Translate the AI-parsed goal
    goal_key = goal_name.lower()
    
    # Handle common AI parser results
    if goal_key == "hypertrophy":
        goal_key = "strength"
    if goal_key == "conditioning":
        goal_key = "cardio"
        
    if goal_key not in category_map:
        goal_key = "strength" # Default to 'strength'
    
    category_id = category_map.get(goal_key, 10) # Default to ID 10
    
    print(f"Fetching wger exercises for goal='{goal_key}' (category_id={category_id})")
    
    params = {
        "category": category_id,
        "language": LANGUAGE_ID,
        "limit": limit,
        "status": 2, # "Approved" exercises
    }
    
    try:
        res = requests.get(f"{BASE_URL}/exercise/", params=params)
        res.raise_for_status()
        data = res.json()
        
        detailed_exercises = []
        for exercise in data.get('results', []): # 'exercise' is the basic object
            exercise_id = exercise.get('id')
            if exercise_id:
                details = get_exercise_details(exercise_id) # 'details' is the info object
                if details:
                    
                    # --- THIS IS THE FIX ---
                    # The 'details' object (from /exerciseinfo/) ALREADY 
                    # has the name. We just need to append it directly.
                    # The line I added last time was wrong and is now REMOVED.
                    
                    detailed_exercises.append(details)
                    # --- END OF FIX ---
                    
        return detailed_exercises
        
    except Exception as e:
        print(f"Error fetching wger exercises: {e}")
        return []

@lru_cache(maxsize=128)
def get_exercise_details(exercise_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetches the detailed information (instructions, muscles)
    for a single exercise ID.
    """
    print(f"Fetching details for exercise_id={exercise_id}...")
    try:
        res = requests.get(f"{BASE_URL}/exerciseinfo/{exercise_id}/")
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"Error fetching exercise details: {e}")
        return None#