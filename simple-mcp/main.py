# main.py

# --- Load .env file FIRST ---
from dotenv import load_dotenv
load_dotenv()
# --- End of Load ---

from fastapi import FastAPI, Request
from utils.ai_client import ask_ai, client 
import os
import requests
import re
import json
from typing import Optional, List, Dict, Any
from functools import lru_cache
from dataclasses import dataclass 

# Use the shared multi-item parser
from utils.multi_parser import (
    extract_product_search_terms_multi, 
    get_multi_context, 
    get_usda_nutrition, 
    get_openfoodfacts_info, 
    get_food_recalls_for_term
)

# --- Import the rules-based recommender ---
from utils.workout_recommender import recommend_workout_from_context, WorkoutPlan

app = FastAPI()

# --- Environment variables ---
OPENFDA_API_KEY = os.getenv("OPENFDA_API_KEY")
USDA_API_KEY = os.getenv("USDA_API_KEY")


# --- START: Macro parsing helpers (Copied from workout_recommender) ---
@dataclass
class Macros:
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    sugars_g: Optional[float] = None
    fat_g: Optional[float] = None

_MACRO_KEYS = {
    "protein": ["protein"],
    "carbs": ["carbs", "carbohydrate, by difference", "carbohydrates"],
    "sugars": ["sugars", "total sugars", "sugar"],
    "fat": ["fat", "total lipid (fat)", "lipid", "total fat"],
}

def _extract_number(unit_str: str) -> Optional[float]:
    if not unit_str:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)", unit_str)
    return float(m.group(1)) if m else None

def _find_line_for_key(text: str, keys: List[str]) -> Optional[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        low = ln.lower()
        for k in keys:
            if k in low:
                return ln
    return None

def parse_macros_from_context(context: str) -> Macros:
    ctx_low = context.lower()

    def grab(keys: List[str]) -> Optional[float]:
        line = _find_line_for_key(context, keys)
        if not line:
            for k in keys:
                m = re.search(rf"{re.escape(k)}\s*[:\-]\s*([^\n\r]+)", ctx_low, flags=re.IGNORECASE)
                if m:
                    return _extract_number(m.group(1))
            return None
        m = re.search(r"[:\-]\s*([^\n\r]+)", line)
        if m:
            return _extract_number(m.group(1))
        return _extract_number(line)

    return Macros(
        protein_g=grab(_MACRO_KEYS["protein"]),
        carbs_g=grab(_MACRO_KEYS["carbs"]),
        sugars_g=grab(_MACRO_KEYS["sugars"]),
        fat_g=grab(_MACRO_KEYS["fat"]),
    )
# --- END: Macro parsing helpers ---


# --- AI-as-a-Parser Function ---
def extract_workout_params_with_ai(query: str) -> dict:
    print(f"Parsing query with AI: {query}")
    
    prompt = f"""
You are a parameter extractor. Analyze the user's query and return a JSON object
with the following keys: 'goal', 'minutes', and 'experience'.

- 'goal': (string) Must be one of: "strength", "hypertrophy", "conditioning", "balance", "fat loss".
- 'minutes': (integer) The number of minutes. Default to 30 if not specified.
- 'experience': (string) Must be one of: "beginner", "intermediate", "advanced". Default to "beginner".

USER QUERY:
"{query}"

JSON:
"""
    
    try:
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        text = response.text.strip().lstrip("```json").rstrip("```")
        params = json.loads(text)
        
        params.setdefault("goal", "balance")
        params.setdefault("minutes", 30)
        params.setdefault("experience", "beginner")
        
        return params
        
    except Exception as e:
        print(f"Error parsing workout params with AI: {e}")
        return {"goal": "balance", "minutes": 30, "experience": "beginner"}


# --- Polishing Fix: Helper to format the WorkoutPlan object ---
def _format_plan_as_markdown(plan: WorkoutPlan) -> str:
    """Converts the WorkoutPlan dataclass into a formatted markdown string."""
    
    # Line 1: Main Title
    title = f"### Your {plan.focus.capitalize()} Workout"
    
    # Line 2: Sub-details (now includes experience)
    details = f"**{plan.intensity.capitalize()}** | **{plan.minutes} min** | **Level: {plan.experience.capitalize()}**"
    
    # Line 3: Fuel Info (if it exists)
    fuel_line = ""
    fuel_parts = []
    if plan.product_hint:
        hint = plan.product_hint
        if len(hint) > 50:
            hint = hint[:50] + "..."
        # FIX 1: Removed italics (*) for a cleaner look
        fuel_parts.append(f"Fueled by: {hint}") 
        
    if plan.kcal_est is not None:
        # FIX 1: Removed italics (*) for a cleaner look
        fuel_parts.append(f"Est. Meal: ~{int(round(plan.kcal_est))} kcal") 
    
    if fuel_parts:
        fuel_line = " • ".join(fuel_parts) 
    
    # Create formatted lists for each section
    warmup_list = "\n".join(f"- {w}" for w in plan.warmup)
    blocks_list = "\n".join(f"- {b}" for b in plan.blocks)
    cooldown_list = "\n".join(f"- {c}"for c in plan.cooldown)

    # --- ASSEMBLE THE FINAL MARKDOWN ---
    # FIX 2: Added a blank line (\n) before --- to fix the Rationale header bug.
    return f"""
{title}
{details}
{fuel_line}

**Rationale:** {plan.rationale}

---
#### 1. Warm-up
{warmup_list}
#### 2. Workout
{blocks_list}
#### 3. Cool-down
{cooldown_list}
---
**Nutrition Tip:** {plan.nutrition_tip}
"""
# --- END OF FIX ---


# --- Endpoints ---

@app.get("/")
def home():
    return {"message": "Food Safety & Nutrition MCP server is live! Use POST /query to test."}


@app.post("/query")
async def handle_query(request: Request):
    body = await request.json()
    query = body.get("query", "") or ""
    query_lower = query.lower()

    wants_recall = "recall" in query_lower
    wants_nutrition = any(k in query_lower for k in ["nutrition", "ingredients", "allergens", "info"])
    
    workout_pattern = re.compile(r"\b(workout|work out|exercise|train|gym)\b", re.IGNORECASE)
    wants_workout = bool(workout_pattern.search(query_lower))

    items = extract_product_search_terms_multi(query)
    items = [i for i in items if i] 

    if wants_workout:
        print(f"AGGREGATE workout intent detected for query: {query}")
        
        total_macros = Macros(protein_g=0.0, carbs_g=0.0, fat_g=0.0, sugars_g=0.0)
        context_blocks = []
        food_items_found = []
        
        stop_phrase_pattern = re.compile(r"^(how|what|should|i|me|my|work|out|train|gym|exercise)", re.IGNORECASE)

        for item in items:
            if stop_phrase_pattern.search(item):
                continue 
            
            food_items_found.append(item)
            print(f"Fetching nutrition for item: {item}")
            
            nutrition_context = get_usda_nutrition(item)
            if nutrition_context is None:
                nutrition_context = get_openfoodfacts_info(item)
            
            if nutrition_context:
                context_blocks.append(nutrition_context)
                macros = parse_macros_from_context(nutrition_context)
                
                total_macros.protein_g += macros.protein_g or 0.0
                total_macros.carbs_g += macros.carbs_g or 0.0
                total_macros.fat_g += macros.fat_g or 0.0
                total_macros.sugars_g += macros.sugars_g or 0.0
            else:
                context_blocks.append(f"[No nutrition data found for '{item}']")
        
        if not food_items_found:
            context = "Please specify at least one food to base your workout on (e.g., 'workout after eating pork')."
            return {"query": query, "context": context, "response": "Could not determine a food item in your query.", "type": "error"}

        summary_header = f"""[AGGREGATE MEAL TOTALS (from {len(food_items_found)} items)]
- Total Protein: {total_macros.protein_g:.1f} g
- Total Carbs: {total_macros.carbs_g:.1f} g
- Total Fat: {total_macros.fat_g:.1f} g
- Total Sugars: {total_macros.sugars_g:.1f} g
"""
        individual_items = "\n\n---\n\n".join(context_blocks)
        total_kcal = (total_macros.protein_g * 4) + (total_macros.carbs_g * 4) + (total_macros.fat_g * 9)
        final_context = f"{summary_header}\n\n{individual_items}"

        print("Using AI to extract parameters...")
        params = extract_workout_params_with_ai(query)
        print(f"AI-extracted params: {params}")
        
        product_hint = " & ".join(food_items_found)
        
        plan_object = recommend_workout_from_context(
            context=final_context, 
            goal=params.get("goal"),
            minutes=params.get("minutes"),
            experience=params.get("experience"),
            product_hint=product_hint
        )
        
        plan_object.kcal_est = total_kcal
        
        generated_plan = _format_plan_as_markdown(plan_object)

        return {
            "query": query,
            "context": final_context, 
            "response": generated_plan,
            "type": "plan" 
        }

    # --- This multi-item and single-item logic remains unchanged ---
    if len(items) >= 2:
        print(f"Multi-item intent detected: {items}")
        multi_context_map = get_multi_context(
            items=items,
            do_recall=wants_recall,
            do_nutrition=wants_nutrition or not wants_recall 
        )
        context_blocks = list(multi_context_map.values())
        context = "\n\n".join(context_blocks) if context_blocks else "No context found for the listed items."
        ai_response = ask_ai(query, context)
        return {
            "query": query,
            "items_detected": items,
            "context": context,
            "response": ai_response,
            "type": "ai"
        }

    search_term = items[0] if items else None

    context = ""
    if wants_recall:
        term = search_term or "food" 
        print(f"Single recall intent for: {term}")
        context = get_food_recalls_for_term(term)
    
    elif wants_nutrition:
        if not search_term:
            context = "Please specify a product name to look up."
        else:
            print(f"Single nutrition intent for: {search_term}")
            context = get_usda_nutrition(search_term)
            if context is None:
                context = get_openfoodfacts_info(search_term)
            if context is None:
                context = f"Couldn't fetch product data for '{search_term}' from any source."
    else:
        context = "No food recall, nutrition, or workout intent was found. Answering generally."

    ai_response = ask_ai(query, context)
    return {
        "query": query,
        "context": context,
        "response": ai_response,
        "type": "ai"
    }