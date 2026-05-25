from datetime import date


def test_login(username: str, password: str) -> None:
    """Attempt MFP login; raises on failure."""
    import myfitnesspal
    client = myfitnesspal.Client(username, password)
    today = date.today()
    client.get_date(today.year, today.month, today.day)


def fetch_today(username: str, enc_pass: str) -> list[dict]:
    """Fetch today's MFP diary and return list of meal dicts."""
    from crypto_utils import decrypt
    import myfitnesspal

    password = decrypt(enc_pass)
    client = myfitnesspal.Client(username, password)
    today = date.today()
    day = client.get_date(today.year, today.month, today.day)

    meals = []
    for meal in (day.meals or []):
        totals = meal.totals or {}
        if not totals:
            continue
        meals.append({
            "description": meal.name or "Meal",
            "calories": round(float(totals.get("calories", 0))),
            "protein_g": round(float(totals.get("protein", 0)), 1),
            "carbs_g": round(float(totals.get("carbohydrates", 0)), 1),
            "fat_g": round(float(totals.get("fat", 0)), 1),
            "macro_source": "myfitnesspal",
        })
    return meals
