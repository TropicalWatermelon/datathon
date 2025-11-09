from fastapi import FastAPI, Request
from dotenv import load_dotenv
from typing import Optional, Dict, Any, List
import re

# LLM glue (optional)
from utils.ai_client import ask_ai

# USDA helpers
from helpers_usda import (
    _fdc_search_json,        # raw FDC search (JSON)
    _fdc_search_multi,       # multi-datatype search
    _pick_best_fdc_hit,      # ranking
    _fsis_recalls_json,      # FSIS open-data recalls (JSON)
)

# Ensure .env is loaded for anything that reads env on import (defensive)
load_dotenv()

app = FastAPI()


# ------------------------
# Root
# ------------------------
@app.get("/")
def home():
    return {
        "message": "Food Safety server is live.",
        "try": [
            "/fdc/search?q=apple",
            "/fdc/food/1102647",
            "/fsis/recalls?status=Active",
        ],
    }


# ------------------------
# Unified LLM-style endpoint
# ------------------------
@app.post("/query")
async def handle_query(request: Request):
    body = await request.json()
    query: str = body.get("query", "")

    qlow = query.lower()
    if any(k in qlow for k in ["recall", "safety", "fsis", "alert"]):
        context = fsis_recall_context(query)
    elif any(k in qlow for k in ["nutrition", "fdc", "nutrient", "calorie", "ingredient"]):
        context = fdc_context(query)
    else:
        context = (
            "No USDA context found. Try recalls (e.g., 'any chicken recalls?') "
            "or nutrition (e.g., 'nutrition for apple')."
        )

    # Optional: feed context to your LLM
    ai_response = ask_ai(query, context)
    return {"query": query, "context": context, "response": ai_response}


# ------------------------
# FDC pass-through routes (useful for debugging)
# ------------------------
@app.get("/fdc/search")
def fdc_search(q: str, dataType: Optional[str] = None, pageSize: int = 5) -> Dict[str, Any]:
    return _fdc_search_json(q, data_type=dataType, page_size=pageSize)

@app.get("/fdc/food/{fdc_id}")
def fdc_food(fdc_id: int) -> Dict[str, Any]:
    # simple passthrough convenience
    from helpers_usda import FDC_BASE, _require_api_key
    api_key = _require_api_key()
    import requests
    r = requests.get(
        f"{FDC_BASE}/food/{fdc_id}",
        params={"api_key": api_key},
        timeout=15
    )
    r.raise_for_status()
    return r.json()


# ------------------------
# Recall route (FSIS open data)
# ------------------------
@app.get("/fsis/recalls")
def fsis_recalls(status: Optional[str] = None, query: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
    try:
        return _fsis_recalls_json(query=query, status=status, limit=limit)
    except Exception as e:
        return {"error": f"FSIS recall fetch failed: {e}"}


# ------------------------
# Context builders (string outputs)
# ------------------------
STOP_WORDS = {
    "nutrition", "nutritional", "calorie", "calories", "macro", "macros",
    "facts", "info", "information", "data", "content", "about", "for", "of"
}

def extract_food_term(q: str) -> Optional[str]:
    """
    Pulls the word(s) after 'for' or 'of' if present.
    """
    m = re.search(r"(?:for|of)\s+([A-Za-z0-9 \-_.]+)", q, flags=re.I)
    return m.group(1).strip() if m else None

def _normalize_food_term(raw: str) -> str:
    """
    Strip punctuation and remove stop-words like 'nutrition' so
    'chicken nutrition' -> 'chicken'.
    """
    cleaned = re.sub(r"[^\w\s\-]", " ", raw.lower()).strip()
    tokens = [t for t in cleaned.split() if t not in STOP_WORDS]
    return " ".join(tokens) if tokens else raw.split()[0]

def fdc_context(query: str) -> str:
    raw = extract_food_term(query) or query
    term = _normalize_food_term(raw)

    try:
        hits = _fdc_search_multi(term, per_type=8)
        if not hits:
            # Fallback: try last token (e.g., from "grilled chicken breast" -> "breast" / "chicken")
            last = term.split()[-1]
            if last != term:
                hits = _fdc_search_multi(last, per_type=8)
                term = last
    except Exception as e:
        return f"Error fetching FDC context: {e}"

    best = _pick_best_fdc_hit(term, hits)
    if not best:
        return f"No FDC results for '{term}'."

    fdc_id = best.get("fdcId")
    desc = best.get("description")
    brand = best.get("brandOwner")
    nutrients = best.get("foodNutrients") or []

    wanted = {"Energy", "Protein", "Total lipid (fat)", "Carbohydrate, by difference", "Sodium, Na"}
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

def fsis_recall_context(query: str) -> str:
    term = extract_food_term(query) or query
    try:
        data = _fsis_recalls_json(query=term, limit=3)
    except Exception as e:
        return f"Error fetching FSIS recalls: {e}"

    hits = data.get("results", [])
    if not hits:
        return f"No recent FSIS recalls found for '{term}'."

    parts = [f"{h.get('title')} (Class={h.get('risk_level')}, Status={h.get('status')})" for h in hits]
    return " | ".join(parts)
