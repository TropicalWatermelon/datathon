from typing import Optional, Dict, Any, List
import os, requests

USDA_API_KEY = os.getenv("USDA_API_KEY")
FDC_BASE = "https://api.nal.usda.gov/fdc/v1"

class FDCError(Exception): pass
class FSISError(Exception): pass

def _fdc_search_json(q: str, data_type: Optional[str]=None, page_size: int=5) -> Dict[str, Any]:
    if not USDA_API_KEY:
        raise FDCError("USDA_API_KEY not set")
    params: Dict[str, Any] = {"api_key": USDA_API_KEY, "query": q, "pageSize": page_size}
    if data_type:
        params["dataType"] = data_type
    r = requests.get(f"{FDC_BASE}/foods/search", params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise FDCError(f"Unexpected FDC response type: {type(data).__name__}")
    return data

# ---- smarter search & ranking ----
_FDC_DATATYPES_PRIORITIZED = ["Survey (FNDDS)", "Foundation", "SR Legacy", "Branded"]

def _fdc_search_multi(term: str, per_type: int = 5) -> List[Dict[str, Any]]:
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
    if not foods: return None
    filtered = [
        f for f in foods
        if term.lower() in str(f.get("description","")).lower()
        or term.lower() in str(f.get("ingredients","")).lower()
        or not any(isinstance(f.get(k), str) for k in ("description","ingredients"))
    ]
    candidates = filtered if filtered else foods
    return max(candidates, key=lambda f: _score_fdc_hit(term, f))

# ---- FSIS recalls ----
FSIS_DATA_URL = "https://data.fsis.usda.gov/resource/recalls.json"

def _fsis_recalls_json(query: Optional[str]=None, status: Optional[str]=None, limit: int=5) -> Dict[str, Any]:
    params: Dict[str, Any] = {"$limit": limit}
    if status: params["status"] = status
    if query:  params["$q"] = query
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
        if not isinstance(it, dict): continue
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
