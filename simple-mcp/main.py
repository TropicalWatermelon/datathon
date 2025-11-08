from fastapi import FastAPI, Request
from dotenv import load_dotenv
from typing import Optional, Dict, Any, List
from helpers_usda import _fdc_search_json, _fdc_search_multi, _score_fdc_hit, _pick_best_fdc_hit, FDCError, _fsis_recalls_json
import os, requests, re

# If you still want LLM responses:
from utils.ai_client import ask_ai

load_dotenv()
app = FastAPI()

class FDCError(Exception):
    pass


# --- ENV ---
USDA_API_KEY = os.getenv("USDA_API_KEY")  # FDC key (Data.gov)
FSIS_BASE = os.getenv("FSIS_BASE", "https://www.fsis.usda.gov")

# --- ROOT ---
@app.get("/")
def home():
    return {"message": "Food Safety server is live. POST /query, or try /fdc/search?q=apple and /fsis/recalls?status=active"}

# --- Main unified endpoint (keeps your shape) ---
@app.post("/query")
async def handle_query(request: Request):
    body = await request.json()
    query: str = body.get("query", "")

    qlow = query.lower()
    context: str

    if any(k in qlow for k in ["recall", "safety", "fsis", "alert"]):
        context = fsis_recall_context(query)
    elif any(k in qlow for k in ["nutrition", "fdc", "nutrient", "calorie", "ingredient"]):
        context = fdc_context(query)
    else:
        context = "No USDA context found. Try asking about recalls (e.g., 'any chicken recalls?') or FDC nutrition (e.g., 'nutrition for apple')."

    ai_response = ask_ai(query, context)  # optional; remove if not needed
    return {"query": query, "context": context, "response": ai_response}

# ------------------------
# USDA FoodData Central (FDC)
# Docs: https://fdc.nal.usda.gov/api-guide
# ------------------------

FDC_BASE = "https://api.nal.usda.gov/fdc/v1"

@app.get("/fdc/search")
def fdc_search(q: str, dataType: Optional[str] = None, pageSize: int = 5):
    """
    Example: /fdc/search?q=apple&dataType=Survey%20(FNDDS)
    """
    if not USDA_API_KEY:
        return {"error": "USDA_API_KEY not set"}
    params = {"api_key": USDA_API_KEY, "query": q, "pageSize": pageSize}
    if dataType:
        params["dataType"] = dataType
    r = requests.get(f"{FDC_BASE}/foods/search", params=params, timeout=15)
    r.raise_for_status()
    return r.json()

@app.get("/fdc/food/{fdc_id}")
def fdc_food(fdc_id: int):
    """
    Example: /fdc/food/1102647
    """
    if not USDA_API_KEY:
        return {"error": "USDA_API_KEY not set"}
    r = requests.get(f"{FDC_BASE}/food/{fdc_id}", params={"api_key": USDA_API_KEY}, timeout=15)
    r.raise_for_status()
    return r.json()

def fsis_recall_context(query: str) -> str:
    term = extract_food_term(query) or query
    try:
        data = _fsis_recalls_json(query=term, limit=3)
    except Exception as e:
        return f"Error fetching FSIS recalls: {e}"

    # Guaranteed dict from helper
    hits = data.get("results", [])
    if not hits:
        return f"No recent FSIS recalls found for '{term}'."

    parts = []
    for h in hits:
        parts.append(f"{h.get('title')} (Class={h.get('risk_level')}, Status={h.get('status')})")
    return " | ".join(parts)


# ----- FDC: smarter search & ranking -----

# Prefer non-branded datasets first to avoid PowerBar-type hits
_FDC_DATATYPES_PRIORITIZED = [
    "Survey (FNDDS)",   # common foods as consumed
    "Foundation",       # curated single-ingredient items
    "SR Legacy",        # legacy SR data
    "Branded",          # LAST: packaged/brand items
]

def fdc_context(query: str) -> str:
    term = extract_food_term(query) or query
    try:
        hits = _fdc_search_multi(term, per_type=8)
    except Exception as e:
        return f"Error fetching FDC context: {e}"

    best = _pick_best_fdc_hit(term, hits)
    if not best:
        return f"No FDC results for '{term}'."

    fdc_id = best.get("fdcId")
    desc = best.get("description")
    brand = best.get("brandOwner")
    nutrients = best.get("foodNutrients") or []

    wanted = {
        "Energy",
        "Protein",
        "Total lipid (fat)",
        "Carbohydrate, by difference",
        "Sodium, Na",
    }
    picked: List[str] = []
    if isinstance(nutrients, list):
        for n in nutrients:
            if isinstance(n, dict) and n.get("nutrientName") in wanted:
                unit = n.get("unitName", "")
                val = n.get("value")
                picked.append(f"{n.get('nutrientName')}: {val}{unit}")

    core = ", ".join(picked[:4]) if picked else "Key nutrients not available."
    brand_part = f" ({brand})" if brand else ""
    return f"FDC Match for '{term}': {desc}{brand_part}, fdcId={fdc_id}. {core}"



def extract_food_term(q: str) -> Optional[str]:
    # Minimal heuristic: pull the word after 'for' or 'of' if present; else return None
    m = re.search(r"(?:for|of)\s+([A-Za-z0-9 \-\_]+)", q, flags=re.I)
    return m.group(1).strip() if m else None



# ------------------------
# USDA FSIS Recall API
# Docs/landing: https://www.fsis.usda.gov/science-data/developer-resources/recall-api
# ------------------------

@app.get("/fsis/recalls")
def fsis_recalls(status: Optional[str] = None, query: Optional[str] = None, limit: int = 5):
    """
    Example: /fsis/recalls?status=active
             /fsis/recalls?query=chicken
    The FSIS API supports attribute-based querying and returns JSON.
    """
    # The documented endpoint is exposed via the FSIS site; use search or filters.
    # We'll use a simple keyword search parameter 'query' if available.
    # If FSIS updates endpoints, adjust here.
    try:
        # Public search endpoint (content search) returns recall pages; for production,
        # use the official Recall API JSON endpoint when provided by FSIS docs.
        url = f"{FSIS_BASE}/api/recalls"  # canonical placeholder per FSIS Recall API docs
        params: Dict[str, Any] = {}
        if status:
            params["status"] = status  # e.g., 'active'
        if query:
            params["q"] = query

        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        # Normalize the top N to a compact list
        items = data if isinstance(data, list) else data.get("results", [])
        simplified: List[Dict[str, Any]] = []
        for it in items[:limit]:
            simplified.append({
                "title": it.get("title") or it.get("recall_title") or it.get("headline"),
                "recall_number": it.get("recall_number") or it.get("id"),
                "status": it.get("status"),
                "risk_level": it.get("risk_level") or it.get("class"),
                "reason": it.get("reason") or it.get("reason_for_recall"),
                "date": it.get("date") or it.get("publication_date") or it.get("start_date"),
                "link": it.get("url") or it.get("link")
            })
        return {"results": simplified}
    except Exception as e:
        return {"error": f"FSIS recall fetch failed: {e}"}
    

# --- FSIS helper & guarded versions ---

class FSISError(Exception):
    pass



