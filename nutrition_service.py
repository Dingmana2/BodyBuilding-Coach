import os
import httpx

from claude_service import estimate_meal_macros

NUTRITIONIX_APP_ID = os.getenv("NUTRITIONIX_APP_ID", "")
NUTRITIONIX_API_KEY = os.getenv("NUTRITIONIX_API_KEY", "")


async def lookup_food_macros(description: str) -> dict:
    """Query Nutritionix NLP endpoint; fall back to Claude Haiku if unavailable."""
    if NUTRITIONIX_APP_ID and NUTRITIONIX_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://trackapi.nutritionix.com/v2/natural/nutrients",
                    headers={
                        "x-app-id": NUTRITIONIX_APP_ID,
                        "x-app-key": NUTRITIONIX_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={"query": description},
                )
            if resp.status_code == 200:
                foods = resp.json().get("foods", [])
                if foods:
                    return _aggregate_nutritionix(foods)
        except Exception:
            pass

    # Fallback: Claude Haiku estimate
    result = estimate_meal_macros(description)
    result["source"] = "estimated"
    result["items"] = []
    return result


def _aggregate_nutritionix(foods: list) -> dict:
    return {
        "calories": round(sum(f.get("nf_calories", 0) for f in foods)),
        "protein_g": round(sum(f.get("nf_protein", 0) for f in foods), 1),
        "carbs_g": round(sum(f.get("nf_total_carbohydrate", 0) for f in foods), 1),
        "fat_g": round(sum(f.get("nf_total_fat", 0) for f in foods), 1),
        "source": "nutritionix",
        "items": [f.get("food_name", "") for f in foods],
    }
