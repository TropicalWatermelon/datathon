from typing import Optional, Dict, Any, List
import os, requests
from dotenv import load_dotenv

# Load .env defensively here too (prevents None keys if caller forgets)
load_dotenv()

FDC_BASE = "https://api.nal.usda.gov/fdc/v1"
FSIS_DATA_URL = "https://data.fsis.usda.gov/resource/recalls.json"

class FDCError(Exception): ...
class FSISError(Exception): ...

def _require_api_key() -> str:
    api_key = os.getenv("USDA_API_KEY")
    if not api_key:
        raise FDCError("USDA_API_KEY not set")
    return api_key

def _fdc_search_json(q: str, data_type: Optional[str] = None, page_size: int = 5) -> Dict[str, Any]:
    """
    Raw FDC /foods/search. Always returns a dict or raises FDCError.
    """
    api_key = _require_api_key()
    params: Dict[str, Any] = {"api_key": api_key, "query": q, "pageSize": page_size}
    if data_type:
        params["dataType"] = data_type
    r = requests.get(f"{FDC_BASE}/foods/search", params=params, timeout=15)
    try:
        r.raise_for_status()
    except requests.RequestException as e:
        raise FDCError(f"FDC HTTP error: {e}") from e
    try:
        data = r.json()
    except ValueError as e:
        raise FDCError("FDC returned non-JSON") from e
    if not isinstance(data, dict):
        raise FDCError(f"Unexpected FDC response type: {type(data).__name__}")
    return data

# ---- smarter search & ranking ----
_FDC_DATATYPES_PRIORITIZED = ["Survey (FNDDS)", "Foundation", "SR Legacy", "Branded"]

def _fdc_search_multi(term: str, per_type: int = 5) -> List[Dict[str, Any]]:
    """
    Query multiple FDC dataTypes (prioritized) and merge results.
    """
    all_hits: List[Dict[str, Any]] = []
    for dt in _FDC_DATATYPES_PRIORITIZED:
        try:
            data = _fdc_search_json(term, data_type=dt, page_size=per_type)
        except Exception:
            continue
        foods = data.get("foods") or []
        if isinstance(foods, list):
            all_hits.extend([f for f in foods if isinstance(f, dict)])
    return all_hits

def _score_fdc_hit(term: str, f: Dict[str, Any]) -> float:
    """
    Heuristic score: favor exact/clean matches, penalize irrelevant branded/supplement items.
    """
    t = term.strip().lower()
    desc = str(f.get("description") or "").lower()
    brand = f.get("brandOwner")
    ing = str(f.get("ingredients") or "").lower()
    score = 0.0
    if desc == t or desc.startswith(t): score += 10
    if t in desc: score += 6
    if t and t in ing: score += 4
    score += -2 if brand else 1
    cat = str(f.get("foodCategory") or "").lower()
    if t in cat: score += 2
    if any(bt in desc for bt in ["bar","powder","supplement","shake","cereal","snack","energy"]): score -= 3
    return score

def _pick_best_fdc_hit(term: str, foods: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not foods:
        return None
    filtered = [
        f for f in foods
        if term.lower() in str(f.get("description","")).lower()
        or term.lower() in str(f.get("ingredients","")).lower()
        or not any(isinstance(f.get(k), str) for k in ("description","ingredients"))
    ]
    candidates = filtered if filtered else foods
    return max(candidates, key=lambda f: _score_fdc_hit(term, f))

# ---- FSIS recalls (open data) ----
def _fsis_recalls_json(query: Optional[str] = None, status: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
    """
    Always returns a dict: {"results": [ simplified items ... ]} or raises FSISError.
    """
    params: Dict[str, Any] = {"$limit": limit}
    if status:
        params["status"] = status  # e.g., "Active"
    if query:
        params["$q"] = query       # full-text search

    try:
        r = requests.get(FSIS_DATA_URL, params=params, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except requests.RequestException as e:
        raise FSISError(f"FSIS HTTP error: {e}") from e
    except ValueError as e:
        raise FSISError("FSIS returned non-JSON") from e

    if not isinstance(raw, list):
        raise FSISError(f"Unexpected FSIS response type: {type(raw).__name__}")

    simplified: List[Dict[str, Any]] = []
    for it in raw[:limit]:
        if not isinstance(it, dict):
            continue
        simplified.append({
            "title": it.get("title") or it.get("recall_title") or it.get("headline"),
            "recall_number": it.get("recall_number") or it.get("id"),
            "status": it.get("status"),
            "risk_level": it.get("classification") or it.get("class"),
            "reason": it.get("reason") or it.get("reason_for_recall"),
            "date": it.get("recall_initiation_date") or it.get("date") or it.get("publication_date") or it.get("start_date"),
            "link": it.get("url") or it.get("link"),
        })

    return {"results": simplified}

# Optional: explicit exports
__all__ = [
    "FDC_BASE",
    "_require_api_key",
    "_fdc_search_json",
    "_fdc_search_multi",
    "_pick_best_fdc_hit",
    "_fsis_recalls_json",
]
