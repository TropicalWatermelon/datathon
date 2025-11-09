# main.py
from fastapi import FastAPI, Request
from utils.ai_client import ask_ai  # Assuming this is in a utils folder
from dotenv import load_dotenv
import os
import requests
import re
from typing import Optional
from functools import lru_cache

from utils.workout_recommender import get_router as get_workout_router

# Use the shared multi-item parser (which already loads spaCy internally)
from utils.multi_parser import extract_product_search_terms_multi, get_multi_context

load_dotenv()

app = FastAPI()
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


@app.post("/query")
async def handle_query(request: Request):
    body = await request.json()
    query = body.get("query", "") or ""
    query_lower = query.lower()

    # Intent detection
    wants_recall = ("recall" in query_lower) or ("recalls" in query_lower)
    wants_nutrition = any(k in query_lower for k in ["nutrition", "ingredients", "info for", "allergens", "info"])

    # Try multi-item extraction first
    items = extract_product_search_terms_multi(query)
    items = [i for i in items if i]  # sanitize

    # If we detected 2+ items, use the multi-item context builder
    if len(items) >= 2:
        multi_context_map = get_multi_context(
            items=items,
            do_recall=wants_recall,
            do_nutrition=wants_nutrition or not wants_recall  # if not recall-only, include nutrition
        )
        context_blocks = list(multi_context_map.values())
        context = "\n\n".join(context_blocks) if context_blocks else "No context found for the listed items."
        ai_response = ask_ai(query, context)
        return {
            "query": query,
            "items_detected": items,
            "context": context,
            "response": ai_response
        }

    # -------- Single-item fallback (uses first parsed term) --------
    context = ""

    if wants_recall:
        # If it's a recall query and we only have 0–1 detected items, keep your original recall flow
        context = get_food_recalls(query)

    elif wants_nutrition:
        # Pick the first plausible product name from the user query
        search_term = _pick_single_term(query)

        if not search_term:
            context = "Please specify a product name to look up."
        else:
            print(f"Searching for product info for: {search_term}")
            print("Trying USDA API first...")
            context = get_usda_nutrition(search_term)

            if context is None:
                print("USDA API failed or found no match. Trying Open Food Facts...")
                context = get_openfoodfacts_info(search_term)

            if context is None:
                context = f"Couldn't fetch product data for '{search_term}' from any source."

    else:
        context = "No food recall or product info context was found for this query."

    ai_response = ask_ai(query, context)
    return {
        "query": query,
        "context": context,
        "response": ai_response
    }


# --- Food Recalls Helper (Cached) ---

@lru_cache(maxsize=128)
def get_food_recalls(query: str) -> str:
    """Fetch recent food recalls from openFDA, using an API key."""
    api_key = OPENFDA_API_KEY
    if not api_key:
        return "openFDA API key not found. Please check your .env file."

    match = re.search(r"(?:recall|recalls)\s(?:on|for|of|about)\s(.+)", query, re.IGNORECASE)
    if match:
        search_term = match.group(1).strip().replace("?", "")
    else:
        search_term = query.replace("recall", "").replace("recalls", "").strip().replace("?", "")
        search_term = re.sub(r"^(is|are|there|any)\s+", "", search_term, flags=re.IGNORECASE).strip()

    if not search_term or len(search_term) < 2:
        search_query = 'status:"Ongoing"'
        context_term = "general"
    else:
        search_query = f'(product_description:"{search_term}" OR reason_for_recall:"{search_term}") AND status:"Ongoing"'
        context_term = f"'{search_term}'"

    print(f"Searching for recalls related to: {context_term}")

    url = "https://api.fda.gov/food/enforcement.json"
    params = {
        "api_key": api_key,
        "search": search_query,
        "limit": 3
    }

    try:
        res = requests.get(url, params=params, timeout=10)

        if res.status_code == 404:
            print("openFDA API returned 404 (No matches found)")
            return f"No recent food recalls found matching {context_term}."

        if res.status_code != 200:
            return f"Couldn't fetch recall data. (status: {res.status_code}, response: {res.text})"

        data = res.json()
        results = data.get("results", [])

        if not results:
            return f"No recent food recalls found matching {context_term}."

        summaries = []
        for item in results:
            product = item.get("product_description", "Unknown product")
            reason = item.get("reason_for_recall", "No reason provided")
            company = item.get("recalling_firm", "Unknown company")
            summaries.append(
                f"**Product:** {product} | **Reason:** {reason} | **Company:** {company}"
            )

        return "Recent Food Recalls: \n- " + "\n- ".join(summaries)

    except Exception as e:
        print("openFDA fetch error:", e)
        return f"Error fetching food recalls: {str(e)}"


# --- Product Info Helper 1 (Fallback) (Cached) ---

@lru_cache(maxsize=128)
def get_openfoodfacts_info(search_term: str) -> Optional[str]:
    """
    Fetch product info from Open Food Facts.
    Returns a formatted string on success, None on failure.
    """
    print(f"CALLING: Open Food Facts API for '{search_term}'")
    url = "https://world.openfoodfacts.org/cgi/search.pl"
    params = {
        "search_terms": search_term,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 1
    }

    try:
        res = requests.get(url, params=params, timeout=10)

        if res.status_code != 200:
            print(f"Open Food Facts API error: Status {res.status_code}")
            return None

        data = res.json()
        products = data.get("products", [])

        if not products:
            print("Open Food Facts: No products found.")
            return None

        product = products[0]
        name = product.get("product_name", "N/A")
        brands = product.get("brands", "N/A")
        ingredients = product.get("ingredients_text", "No ingredients listed.")
        allergens = product.get("allergens_from_ingredients", "No allergens listed.")
        nutriscore = (product.get("nutrition_grade_fr") or "N/A").upper()

        return (
            f"[From Open Food Facts (Fallback)]\n"
            f"**Product:** {name} (by {brands}) | **Nutri-Score:** {nutriscore}\n"
            f"**Ingredients:** {ingredients}\n"
            f"**Allergens:** {allergens}"
        )

    except requests.exceptions.RequestException as e:
        print(f"Open Food Facts fetch error: {e}")
        return None
    except Exception as e:
        print(f"Open Food Facts generic error: {e}")
        return None


# --- Product Info Helper 2 (Primary) (Cached) ---

@lru_cache(maxsize=128)
def get_usda_nutrition(search_term: str) -> Optional[str]:
    """
    Fetch product info from USDA FoodData Central.
    Returns a formatted string on success, None on failure.
    """
    print(f"CALLING: USDA API for '{search_term}'")
    api_key = USDA_API_KEY
    if not api_key:
        print("USDA API key not found.")
        return None

    try:
        search_url = "https://api.nal.usda.gov/fdc/v1/foods/search"
        search_params = {
            "api_key": api_key,
            "query": search_term,
            "dataType": ["Branded", "Foundation"],
            "pageSize": 1
        }

        res_search = requests.get(search_url, params=search_params, timeout=10)

        if res_search.status_code != 200:
            print(f"USDA Search API error: Status {res_search.status_code}")
            return None

        search_data = res_search.json()
        foods = search_data.get("foods", [])

        if not foods:
            print("USDA: No products found.")
            return None

        fdcId = foods[0].get("fdcId")
        if not fdcId:
            return None

        details_url = f"https://api.nal.usda.gov/fdc/v1/food/{fdcId}"
        details_params = {"api_key": api_key}

        res_details = requests.get(details_url, params=details_params, timeout=10)

        if res_details.status_code != 200:
            print(f"USDA Details API error: Status {res_details.status_code}")
            return None

        data = res_details.json()

        name = data.get("description", "N/A")
        brand = data.get("brandOwner", "N/A")
        ingredients = data.get("ingredients", "No ingredients listed.")

        nutrients = {
            (n.get("nutrient", {}) or {}).get("name", ""):
            f"{n.get('amount', 0)} {(n.get('nutrient', {}) or {}).get('unitName', '')}".strip()
            for n in data.get("foodNutrients", [])
        }

        protein = nutrients.get("Protein", "N/A")
        fat = nutrients.get("Total lipid (fat)", "N/A")
        carbs = nutrients.get("Carbohydrate, by difference", "N/A")
        sugars = nutrients.get("Total Sugars", "N/A")

        return (
            f"[From USDA FoodData Central (Primary)]\n"
            f"**Product:** {name} (by {brand})\n"
            f"**Key Nutrients (per 100g):**\n"
            f"  - Protein: {protein}\n"
            f"  - Fat: {fat}\n"
            f"  - Carbs: {carbs}\n"
            f"  - Sugars: {sugars}\n"
            f"**Ingredients:** {ingredients}"
        )

    except requests.exceptions.RequestException as e:
        print(f"USDA fetch error: {e}")
        return None
    except Exception as e:
        print(f"USDA generic error: {e}")
        return None
