# main.py

from fastapi import FastAPI, Request
from utils.ai_client import ask_ai # Assuming this is in a utils folder
from dotenv import load_dotenv
import os
import requests
import re
from typing import Optional
from functools import lru_cache 

# --- NEW: spaCy Imports ---
import spacy
# Load the small model once when the server starts
nlp = spacy.load("en_core_web_sm")
# --- End of NEW ---

load_dotenv()

app = FastAPI()

# --- Environment variables ---
OPENFDA_API_KEY = os.getenv("OPENFDA_API_KEY")
USDA_API_KEY = os.getenv("USDA_API_KEY") 


# --- Endpoints ---

@app.get("/")
def home():
    return {"message": "Food Safety & Nutrition MCP server is live! Use POST /query to test."}


@app.post("/query")
async def handle_query(request: Request):
    body = await request.json()
    query = body.get("query", "")
    query_lower = query.lower() 

    context = ""

    if "recall" in query_lower:
        context = get_food_recalls(query)
        
    elif "nutrition" in query_lower or "ingredients" in query_lower or "info for" in query_lower or "allergens" in query_lower:
        
        # This will now call the new, smarter spaCy function
        search_term = extract_product_search_term(query)
        
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

# --- NEW: Smart Search Term Extractor (using spaCy) ---

def extract_product_search_term(query: str) -> Optional[str]:
    """
    Uses NLP (spaCy) to find the most likely product in the query.
    """
    # Define keywords we want to ignore
    stopwords = {"nutrition", "ingredients", "info", "allergens", "for", "on", 
                 "of", "about", "in", "what", "is", "are", "the", "a", "an"}
    
    doc = nlp(query.lower())
    
    # 1. Check for "noun chunks" (e.g., "peanut butter", "coca cola")
    # This is the most reliable method
    for chunk in doc.noun_chunks:
        # Clean the chunk by removing stopwords
        clean_chunk = " ".join(token.text for token in chunk if token.text not in stopwords)
        
        # If the chunk is more than one word, it's probably a product
        if len(clean_chunk.split()) > 1:
            print(f"NLP (Chunk) found: {clean_chunk}")
            return clean_chunk.strip()

    # 2. If no multi-word chunks, check for single proper nouns (e.g., "Nutella")
    # or just nouns if they aren't stopwords
    for token in reversed(doc): # Go backwards to find the last noun
        if token.pos_ in ["PROPN", "NOUN"] and token.text not in stopwords:
            print(f"NLP (Token) found: {token.text}")
            return token.text

    # 3. Fallback: If NLP fails, just clean the whole string
    # This is similar to our old "dumb" logic
    fallback_term = re.sub(r"(nutrition|ingredients|info|for|on|of|about|in|allergens|what|is|are|the)", 
                           "", query, flags=re.IGNORECASE)
    fallback_term = fallback_term.strip().replace("?", "")
    
    if fallback_term:
        print(f"NLP (Fallback) found: {fallback_term}")
        return fallback_term

    return None # Give up

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
        nutriscore = product.get("nutrition_grade_fr", "N/A").upper()

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
        
        nutrients = {n.get("nutrient", {}).get("name"): f"{n.get('amount', 0)} {n.get('nutrient', {}).get('unitName', '')}" 
                     for n in data.get("foodNutrients", [])}
        
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