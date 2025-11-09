# main.py
from fastapi import FastAPI, Request
from utils.ai_client import ask_ai
from dotenv import load_dotenv
import os
import requests
import re
from typing import Optional
from functools import lru_cache

# --- NEW: Import your workout recommender ---
from utils.workout_recommender import get_router as get_workout_router, recommend_workout_from_context, WorkoutPlan

# Use the shared multi-item parser
from utils.multi_parser import extract_product_search_terms_multi, get_multi_context, get_usda_nutrition, get_openfoodfacts_info, get_food_recalls_for_term

load_dotenv()

app = FastAPI()
# This router is still good to keep if you want to test the /workout endpoint directly
app.include_router(get_workout_router()) 

# --- Environment variables ---
OPENFDA_API_KEY = os.getenv("OPENFDA_API_KEY")
USDA_API_KEY = os.getenv("USDA_API_KEY")


# --- Endpoints ---

@app.get("/")
def home():
    return {"message": "Food Safety & Nutrition MCP server is live! Use POST /query to test."}


def _pick_single_term(query: str) -> Optional[str]:
    """
    Reuse the multi-item extractor but pick the first likely product term.
    """
    items = [i for i in extract_product_search_terms_multi(query) if i]
    return items[0] if items else None


# --- NEW: Helper to format the plan ---
def _format_plan_for_display(plan: WorkoutPlan) -> str:
    """Converts the WorkoutPlan dataclass into a nice markdown string."""
    blocks_str = "\n".join(f"- {b}" for b in plan.blocks)
    warmup_str = "\n".join(f"- {w}" for w in plan.warmup)
    cooldown_str = "\n".join(f"- {c}" for c in plan.cooldown)
    
    return (
        f"### {plan.title}\n"
        f"**Focus:** {plan.focus.capitalize()} ({plan.intensity} intensity)\n\n"
        f"**Rationale:** {plan.rationale}\n\n"
        f"**Warm-up:**\n{warmup_str}\n\n"
        f"**Main Workout:**\n{blocks_str}\n\n"
        f"**Cool-down:**\n{cooldown_str}\n\n"
        f"**Nutrition Tip:** {plan.nutrition_tip}"
    )


@app.post("/query")
async def handle_query(request: Request):
    body = await request.json()
    query = body.get("query", "") or ""
    query_lower = query.lower()

    # --- UPDATED: Intent detection ---
    wants_recall = "recall" in query_lower
    wants_nutrition = any(k in query_lower for k in ["nutrition", "ingredients", "allergens", "info"])
    wants_workout = any(k in query_lower for k in ["workout", "exercise", "train", "gym"])

    items = extract_product_search_terms_multi(query)
    items = [i for i in items if i]  # sanitize

    # --- NEW: Handle workout intent first ---
    if wants_workout:
        print(f"Workout intent detected for query: {query}")
        # Find the food to base the workout on
        search_term = _pick_single_term(query)
        
        if not search_term:
            context = "Please specify a food to base your workout on (e.g., 'workout after eating pork')."
            return {"query": query, "context": context, "response": "Could not determine a food item in your query.", "type": "error"}

        print(f"Basing workout on nutrition for: {search_term}")
        
        # 1. Get the nutrition context
        nutrition_context = get_usda_nutrition(search_term)
        if nutrition_context is None:
            nutrition_context = get_openfoodfacts_info(search_term)
        
        if nutrition_context is None:
            context = f"Couldn't fetch product data for '{search_term}' from any source."
            return {"query": query, "context": context, "response": "Could not get nutrition data to plan workout.", "type": "error"}

        # 2. Call the workout recommender
        # Note: You can add more logic here to get goal/minutes from the query
        plan = recommend_workout_from_context(
            context=nutrition_context,
            product_hint=search_term,
            goal="balance", # default
            minutes=30,     # default
            experience="beginner" # default
        )
        
        # 3. Format the plan as the "response"
        formatted_plan = _format_plan_for_display(plan)
        
        return {
            "query": query,
            "context": nutrition_context,
            "response": formatted_plan,
            "type": "plan" # Tell the UI this is a plan, not an AI response
        }

    # --- Multi-item logic (no change) ---
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

    # --- Single-item nutrition/recall logic (no change) ---
    context = ""
    search_term = _pick_single_term(query)

    if wants_recall:
        term = search_term or "food" # fallback to general
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
        context = "No food recall, nutrition, or workout intent was found for this query."

    ai_response = ask_ai(query, context)
    return {
        "query": query,
        "context": context,
        "response": ai_response,
        "type": "ai"
    }