# utils/workout_recommender.py
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List

try:
    from fastapi import APIRouter, Body
    _FASTAPI_AVAILABLE = True
except Exception:
    _FASTAPI_AVAILABLE = False


# ---------------------------
# Parsing & domain structures
# ---------------------------

@dataclass
class Macros:
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    sugars_g: Optional[float] = None
    fat_g: Optional[float] = None
    kcal: Optional[float] = None 

    def estimated_kcal(self) -> Optional[float]:
        if self.kcal is not None:
            return self.kcal
        kcal = 0.0
        have_any = False
        if self.protein_g is not None:
            kcal += 4.0 * self.protein_g
            have_any = True
        if self.carbs_g is not None:
            kcal += 4.0 * self.carbs_g
            have_any = True
        if self.fat_g is not None:
            kcal += 9.0 * self.fat_g
            have_any = True
        # Return None if no macros were found, 0.0 otherwise
        if not have_any:
            return None
        return kcal


_MACRO_KEYS = {
    "protein": ["protein"],
    "carbs": ["carbs", "carbohydrate, by difference", "carbohydrates"],
    "sugars": ["sugars", "total sugars", "sugar"],
    "fat": ["fat", "total lipid (fat)", "lipid", "total fat"],
    "kcal": ["energy", "calories", "kcal"]
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

    macros = Macros(
        protein_g=grab(_MACRO_KEYS["protein"]),
        carbs_g=grab(_MACRO_KEYS["carbs"]),
        sugars_g=grab(_MACRO_KEYS["sugars"]),
        fat_g=grab(_MACRO_KEYS["fat"]),
        kcal=grab(_MACRO_KEYS["kcal"]),
    )
    return macros


# ---------------------------
# Workout recommendation core
# ---------------------------

@dataclass
class WorkoutPlan:
    # --- Polishing Fix: Separated title data ---
    # title: str  (Removed)
    minutes: int
    intensity: str
    focus: str
    blocks: List[str]
    warmup: List[str]
    cooldown: List[str]
    rationale: str
    nutrition_tip: str
    product_hint: Optional[str] = None # (Added)
    kcal_est: Optional[float] = None   # (Added)
    experience: str = "beginner"       # (Added)
    # --- End of Fix ---


def _choose_focus(macros: Macros, goal: str) -> Tuple[str, str]:
    g = (goal or "balance").lower()
    protein = macros.protein_g or 0.0
    carbs = macros.carbs_g or 0.0
    sugars = macros.sugars_g or 0.0
    fat = macros.fat_g or 0.0
    kcal = macros.estimated_kcal() or 0.0

    # Raised thresholds
    sugar_ratio = (sugars / carbs) if carbs > 1.0 else 0.0
    carb_bias = carbs >= 30  
    protein_bias = protein >= 15
    fat_bias = fat >= 15
    high_sugar = sugars >= 15 and sugar_ratio >= 0.4 

    # Goal overrides
    if g in ["strength", "power"]:
        return ("strength", "You asked for strength, so we’ll bias toward heavier sets and longer rests.")
    if g in ["fat loss", "cut", "lean", "conditioning"]:
        return ("conditioning", "Goal is conditioning/fat loss—more sustained heart rate with intervals.")
    if g in ["muscle", "hypertrophy", "build"]:
        return ("hypertrophy", "Goal is muscle gain—moderate loads, higher volume.")

    # Heuristics
    if carb_bias and not fat_bias:
        return ("conditioning",
                "Higher carbohydrate intake supports glycogen-fueled intervals and aerobic work today.")
    if protein_bias and not high_sugar:
        return ("strength",
                "Higher protein and lower sugar favor productive strength work with solid recovery.")
    if fat_bias and not carb_bias:
        return ("mixed",
                "Higher fats with modest carbs suggest a mixed session: brief intensity plus strength.")
    if high_sugar:
        return ("conditioning",
                "Higher sugar spikes are well-utilized by short-to-mid interval conditioning.")
    
    # Fallback for low-energy foods
    if carbs < 15 and protein < 10 and fat < 10:
        return ("active recovery",
                "This is a very low-energy food/drink. A light active recovery session (like a walk or stretching) is a great choice.")
    
    return ("mixed", "Balanced macros—today suits a mixed session (strength + conditioning).")


def _default_blocks_for_focus(focus: str, minutes: int, experience: str) -> Tuple[List[str], str, str]:
    exp = (experience or "beginner").lower()
    m = max(15, min(75, int(minutes or 30)))

    if focus == "strength":
        main = max(10, int(m * 0.6))
        aux = max(5, int(m * 0.25))
        cond = m - main - aux
        blocks = [
            f"Main Strength ({main} min): 4–5 sets of 3–6 reps on a compound lift "
            f"(e.g., Squat or Deadlift). Rest 2–3 min.",
            f"Accessory ({aux} min): 2–3 exercises, 2–3 sets of 8–12 reps "
            f"(e.g., RDL, Row, Split Squat).",
        ]
        if cond >= 5:
            blocks.append(f"Light Conditioning ({cond} min): brisk walk or easy bike for recovery.")
        rationale = "Strength focus improves neural drive and mechanical tension. Keep form crisp; rest long."
    elif focus == "hypertrophy":
        main = max(10, int(m * 0.5))
        giant = max(5, int(m * 0.35))
        fin = m - main - giant
        blocks = [
            f"Hypertrophy Sets ({main} min): 3–4 sets of 8–12 reps on 1–2 big lifts "
            f"(e.g., Bench + Row). Rest 60–90s.",
            f"Volume Block ({giant} min): 2 rounds of a 3-move tri-set "
            f"(e.g., DB Press, Lat Pulldown, Face Pull), moderate load.",
        ]
        if fin >= 5:
            blocks.append(f"Finisher ({fin} min): sled push or 5-min tempo bike.")
        rationale = "Hypertrophy focus uses moderate loads and volume to maximize muscle stimulus."
    elif focus == "conditioning":
        work = max(8, int(m * 0.5))
        tempo = max(5, int(m * 0.3))
        core = m - work - tempo
        blocks = [
            f"Intervals ({work} min): 8–12 rounds of 30s hard / 60s easy (bike/row/run).",
            f"Tempo ({tempo} min): continuous zone-2/3 effort you can sustain.",
        ]
        if core >= 5:
            blocks.append(f"Core/Carry ({core} min): plank holds + farmer carries.")
        rationale = "Intervals + tempo train aerobic base and lactate clearance; great use of higher carbs/sugars."
        
    elif focus == "active recovery":
        blocks = [
            f"Light Cardio ({int(m*0.4)} min): Easy walk, bike, or elliptical. Should be able to hold a conversation.",
            f"""Mobility ({int(m*0.6)} min): Focus on 3-4 key areas. Examples:
  - 10-15 Cat/Cows
  - 2 min Couch Stretch (each side)
  - 2 min Pigeon Pose (each side)
  - 10-15 T-Spine Rotations"""
        ]
        rationale = "This is a low-energy day, so we'll focus on light movement and mobility to aid recovery and feel good."
        
    else:  # "mixed"
        strength = max(8, int(m * 0.35))
        metcon = max(6, int(m * 0.35))
        mobility = m - strength - metcon
        blocks = [
            f"Strength Primer ({strength} min): 3x5 on one lift (e.g., Front Squat) + 2x10 accessory.",
            f"Short MetCon ({metcon} min): 10-min AMRAP: 8 KB swings, 6 push-ups, 10 air squats.",
        ]
        if mobility >= 5:
            blocks.append(f"Mobility ({mobility} min): hips, T-spine, calves (slow controlled).")
        rationale = "Mixed session balances force production with metabolic conditioning without overreaching."

    if exp in ["beginner", "novice"]:
        warmup = [
            "5 min easy cardio",
            "2 rounds: 10 air squats, 10 band pull-aparts, 10 hip hinges",
        ]
        cooldown = ["3–5 min easy cardio", "2–3 stretches for the trained areas"]
    else:
        warmup = [
            "4–6 min zone-1/2 cardio",
            "Movement prep: dynamic splits, inchworms, band rows",
        ]
        cooldown = ["Breathing down-shift (2–3 min)", "Light mobility in tight areas"]

    intensity = {"strength": "moderate", "hypertrophy": "moderate", "conditioning": "high", "mixed": "moderate", "active recovery": "low"}.get(focus, "low")
    return blocks, rationale, intensity


def _nutrition_tip(macros: Macros, focus: str) -> str:
    protein = macros.protein_g or 0.0
    carbs = macros.carbs_g or 0.0
    sugars = macros.sugars_g or 0.0
    fat = macros.fat_g or 0.0

    if focus in ["strength", "hypertrophy"]:
        tip = "Have 20–35 g protein within ~2 hours post-lift; add 20–60 g carbs if doing volume."
        if protein < 15:
            tip += " (Protein looked low—consider a serving of yogurt/shake/chicken.)"
    elif focus == "conditioning":
        tip = "Sip water; if intervals exceed 25–30 min, add electrolytes. Post-workout carbs (30–60 g) help."
        if sugars >= 15 and carbs >= 30: 
            tip += " (High sugars today—put them to work with those intervals!)"
    
    elif focus == "active recovery":
        tip = "Great job on the light movement. Focus on hydration and a standard, balanced meal."
    
    else: # Mixed
        tip = "Balance plate post-session: lean protein, veggies, and a fist of carbs. Hydrate well."
    return tip


def recommend_workout_from_context(
    context: str,
    goal: str = "balance",
    minutes: int = 30,
    experience: str = "beginner",
    product_hint: Optional[str] = None,
) -> WorkoutPlan:
    macros = parse_macros_from_context(context)
    focus, why = _choose_focus(macros, goal)
    blocks, rationale_core, intensity = _default_blocks_for_focus(focus, minutes, experience)
    
    # --- Polishing Fix: Pass raw data to dataclass ---
    kcal_est = macros.estimated_kcal()

    plan = WorkoutPlan(
        minutes=max(15, min(75, int(minutes or 30))),
        intensity=intensity,
        focus=focus,
        blocks=blocks,
        warmup=[
            "Joint CARs (neck/shoulders/hips) 30–45s each",
            "Light cardio 3–5 min"
        ],
        cooldown=[
            "Nasal breathing 2–3 min (box or 4-7-8)",
            "Targeted mobility 3–5 min"
        ],
        rationale=f"{why} {rationale_core}",
        nutrition_tip=_nutrition_tip(macros, focus),
        product_hint=product_hint, # (Added)
        kcal_est=kcal_est,         # (Added)
        experience=experience      # (Added)
    )
    # --- End of Fix ---
    return plan


# ---------------------------
# Optional FastAPI Router
# ---------------------------

def get_router() -> "APIRouter":
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI not available; install fastapi to use the router.")

    router = APIRouter(prefix="/workout", tags=["workout"])

    @router.post("")
    def plan_workout(
        query: str = Body("", embed=True),
        context: str = Body("", embed=True),
        goal: str = Body("balance", embed=True),
        minutes: int = Body(30, embed=True),
        experience: str = Body("beginner", embed=True),
        product_hint: Optional[str] = Body(None, embed=True),
    ):
        plan = recommend_workout_from_context(
            context=context,
            goal=goal,
            minutes=minutes,
            experience=experience,
            product_hint=product_hint,
        )
        # --- Polishing Fix: Return new dataclass structure ---
        return {
            "query": query,
            "plan": {
                "minutes": plan.minutes,
                "intensity": plan.intensity,
                "focus": plan.focus,
                "warmup": plan.warmup,
                "blocks": plan.blocks,
                "cooldown": plan.cooldown,
                "rationale": plan.rationale,
                "nutrition_tip": plan.nutrition_tip,
                "product_hint": plan.product_hint, 
                "kcal_est": plan.kcal_est,         
                "experience": plan.experience,     
            }
        }
        # --- End of Fix ---

    return router