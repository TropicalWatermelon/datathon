# utils/multi_parser.py
import re
import requests
from typing import List, Optional, Dict
from functools import lru_cache
import os
import spacy

# Load once
nlp = spacy.load("en_core_web_sm")

# ENV
OPENFDA_API_KEY = os.getenv("OPENFDA_API_KEY")
USDA_API_KEY = os.getenv("USDA_API_KEY")

@lru_cache(maxsize=128)
def get_openfoodfacts_info(search_term: str) -> Optional[str]:
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
            return None

        data = res.json()
        products = data.get("products", [])
        if not products:
            return None

        product = products[0]
        name = product.get("product_name", "N/A")
        brands = product.get("brands", "N/A")
        ingredients = product.get("ingredients_text", "No ingredients listed.")
        allergens = product.get("allergens_from_ingredients", "No allergens listed.")
        nutriscore = (product.get("nutrition_grade_fr") or "N/A").upper()

        return (
            f"[Open Food Facts]\n"
            f"**Product:** {name} (by {brands}) | **Nutri-Score:** {nutriscore}\n"
            f"**Ingredients:** {ingredients}\n"
            f"**Allergens:** {allergens}"
        )
    except Exception:
        return None


@lru_cache(maxsize=128)
def get_usda_nutrition(search_term: str) -> Optional[str]:
    
    # Hard-code a response for "water" to avoid bad matches
    if search_term.lower() in ["water", "a glass of water", "glass of water"]:
        print("Returning default context for 'water'")
        return (
            f"[USDA]\n"
            f"**Product:** Water (by N/A)\n"
            f"**Key Nutrients (per 100g):**\n"
            f"   - Protein: 0.0 g\n"
            f"   - Fat: 0.0 g\n"
            f"   - Carbs: 0.0 g\n"
            f"   - Sugars: 0.0 g\n"
            f"**Ingredients:** Water"
        )
    
    api_key = USDA_API_KEY
    if not api_key:
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
            return None

        search_data = res_search.json()
        foods = search_data.get("foods", [])
        if not foods:
            return None

        fdcId = foods[0].get("fdcId")
        if not fdcId:
            return None

        details_url = f"https://api.nal.usda.gov/fdc/v1/food/{fdcId}"
        details_params = {"api_key": api_key}
        res_details = requests.get(details_url, params=details_params, timeout=10)
        if res_details.status_code != 200:
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
            f"[USDA]\n"
            f"**Product:** {name} (by {brand})\n"
            f"**Key Nutrients (per 100g):**\n"
            f"   - Protein: {protein}\n"
            f"   - Fat: {fat}\n"
            f"   - Carbs: {carbs}\n"
            f"   - Sugars: {sugars}\n"
            f"**Ingredients:** {ingredients}"
        )

    except Exception:
        return None


@lru_cache(maxsize=128)
def get_food_recalls_for_term(term: str, limit: int = 3) -> str:
    api_key = OPENFDA_API_KEY
    if not api_key:
        return "openFDA API key missing"

    term_clean = term.strip().replace("?", "")
    if not term_clean:
        return "No term provided for recalls."

    search_query = (
        f'(product_description:"{term_clean}" OR reason_for_recall:"{term_clean}") '
        f'AND status:"Ongoing"'
    )
    url = "https://api.fda.gov/food/enforcement.json"
    params = {"api_key": api_key, "search": search_query, "limit": limit}

    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 404:
            return f"No ongoing recalls for '{term_clean}'."
        if res.status_code != 200:
            return f"Recall fetch failed ({res.status_code})."

        data = res.json()
        results = data.get("results", [])
        if not results:
            return f"No ongoing recalls for '{term_clean}'."

        lines = []
        for item in results:
            product = item.get("product_description", "Unknown")
            reason = item.get("reason_for_recall", "No reason provided")
            firm = item.get("recalling_firm", "Unknown company")
            lines.append(f"- **Product:** {product} | **Reason:** {reason} | **Company:** {firm}")

        return "Ongoing Recalls:\n" + "\n".join(lines)

    except Exception as e:
        return f"Recall fetch error: {e}"


# ---------- Multi-item parsing ----------

_STOPWORDS = {
    "nutrition", "nutritional", "ingredients", "ingredient",
    "info", "information", "allergens", "recall", "recalls",
    
    "workout", "exercise", "train", "gym", "work", "out",
    "excercise", 
    
    "for", "on", "of", "about", "in", "what", "is", "are", "the",
    "a", "an", "i", "ate", "eat", "eating", "should", "how", "me", "my",
    "give", "tell", "show", "can", "after", "please", "and",
    
    # --- Polishing Fix ---
    "drank", "drink", "had", "plan" 
    # --- End of Fix ---
}

_LIST_SEP = re.compile(r",| and | & ", flags=re.IGNORECASE)


def _clean_phrase(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\?", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _is_stop_phrase(phrase: str) -> bool:
    words = phrase.lower().split()
    if not words:
        return True
    return all(word in _STOPWORDS for word in words)

def extract_product_search_terms_multi(query: str, max_terms: int = 6) -> List[str]:
    q = query.strip()
    parts = [p for p in _LIST_SEP.split(q) if p.strip()]
    if not parts or len(parts) == 1:
        parts = [q]

    candidates = []
    for p in parts:
        cleaned = _clean_phrase(p)
        if cleaned and not _is_stop_phrase(cleaned):
            
            # --- Polishing Fix: Clean stopwords from the *beginning* ---
            words = cleaned.split()
            while words and words[0] in _STOPWORDS:
                words.pop(0)
            
            final_phrase = " ".join(words).strip()
            if final_phrase and final_phrase not in candidates:
                candidates.append(final_phrase)
            # --- End of Fix ---
            
    if candidates and len(candidates) > 1:
         return candidates[:max_terms]

    doc = nlp(q.lower())
    for chunk in doc.noun_chunks:
        if _is_stop_phrase(chunk.text):
            continue
        
        phrase = " ".join([t.text for t in chunk if t.text not in _STOPWORDS]).strip()
        if phrase and phrase not in candidates:
            if not any(phrase in c for c in candidates):
                candidates.append(phrase)

    tokens = [t.text for t in doc if t.pos_ in {"NOUN", "PROPN"} and t.text not in _STOPWORDS]
    if not candidates and tokens:
        candidates.extend(tokens)

    seen = set()
    final = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            final.append(p)

    return final[:max_terms]


def get_multi_context(
    items: List[str],
    do_recall=False,
    do_nutrition=True
) -> Dict[str, str]:
    out = {}

    for term in items:
        lines = [f"### {term}"]

        if do_nutrition:
            usda = get_usda_nutrition(term)
            if usda:
                lines.append(usda)
            else:
                off = get_openfoodfacts_info(term)
                lines.append(off or "No product info found")

        if do_recall:
            recalls = get_food_recalls_for_term(term)
            lines.append(recalls)

        out[term] = "\n".join(lines)

    return out