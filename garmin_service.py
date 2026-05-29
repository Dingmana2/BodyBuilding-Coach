import json
import os
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
_CACHE = DATA_DIR / "garmin_cache.json"


def test_login(email: str, password: str) -> None:
    """Attempt login; raises on failure."""
    from garminconnect import Garmin
    client = Garmin(email, password)
    client.login()


def fetch_and_cache(chat_id: int, email: str, enc_pass: str) -> dict:
    """Fetch recovery metrics from Garmin Connect and write to cache."""
    from garminconnect import Garmin
    from crypto_utils import decrypt

    password = decrypt(enc_pass)
    client = Garmin(email, password)
    client.login()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    result: dict = {"chat_id": chat_id, "date": yesterday}

    # Garmin attributes "Thursday night → Friday morning" sleep as Friday's data.
    # Try today first so Friday check-ins get the correct night's sleep.
    for sleep_date in (today, yesterday):
        try:
            sleep = client.get_sleep_data(sleep_date) or {}
            dto = sleep.get("dailySleepDTO") or {}
            secs = dto.get("sleepTimeSeconds") or 0
            if not secs:
                continue
            result["sleep_duration_hrs"] = round(secs / 3600, 2)
            scores = sleep.get("sleepScores") or {}
            overall = (scores.get("overall") or {}).get("value")
            if overall is not None:
                result["sleep_score_1_10"] = max(1, min(10, round(overall / 10)))
            deep_secs = dto.get("deepSleepSeconds") or 0
            rem_secs = dto.get("remSleepSeconds") or 0
            light_secs = dto.get("lightSleepSeconds") or 0
            if deep_secs:
                result["deep_sleep_mins"] = round(deep_secs / 60)
            if rem_secs:
                result["rem_sleep_mins"] = round(rem_secs / 60)
            if light_secs:
                result["light_sleep_mins"] = round(light_secs / 60)
            break
        except Exception:
            pass

    try:
        hrv = client.get_hrv_data(yesterday) or {}
        summary = hrv.get("hrvSummary") or {}
        hrv_val = summary.get("weeklyAvg") or summary.get("lastNight")
        if hrv_val:
            result["hrv_ms"] = round(float(hrv_val), 1)
    except Exception:
        pass

    try:
        rhr_resp = client.get_rhr_day(yesterday) or {}
        metrics = (rhr_resp.get("allMetrics") or {}).get("metricsMap") or {}
        rhr_list = metrics.get("WELLNESS_RESTING_HEART_RATE") or []
        if rhr_list:
            rhr_val = rhr_list[0].get("value")
            if rhr_val:
                result["resting_hr_bpm"] = int(rhr_val)
    except Exception:
        pass

    try:
        stress_resp = client.get_stress_data(yesterday) or {}
        avg = None
        if isinstance(stress_resp, dict):
            avg = stress_resp.get("avgStressLevel")
        elif isinstance(stress_resp, list) and stress_resp:
            readings = [s.get("stressLevel", -1) for s in stress_resp if s.get("stressLevel", -1) > 0]
            if readings:
                avg = sum(readings) / len(readings)
        if avg is not None and avg > 0:
            result["stress_score_1_10"] = max(1, min(10, round((100 - avg) / 10)))
    except Exception:
        pass

    # Body Battery — end-of-day value (Garmin's own energy reserve metric, 0-100)
    try:
        bb_data = client.get_body_battery(yesterday, yesterday) or []
        if isinstance(bb_data, list) and bb_data:
            bb_val = bb_data[-1].get("value") if isinstance(bb_data[-1], dict) else None
            if bb_val is not None:
                result["body_battery_end"] = int(bb_val)
    except Exception:
        pass

    # Respiratory rate during sleep
    try:
        resp = client.get_respiration_data(yesterday) or {}
        avg_resp = resp.get("avgWakingRespirationValue") or resp.get("lowestRespirationValue")
        if avg_resp:
            result["avg_respiration_rpm"] = round(float(avg_resp), 1)
    except Exception:
        pass

    # SpO2 (blood oxygen saturation)
    try:
        spo2 = client.get_pulse_ox_data(yesterday) or {}
        avg_spo2 = spo2.get("averageSpO2")
        if avg_spo2:
            result["avg_spo2_pct"] = round(float(avg_spo2), 1)
    except Exception:
        pass

    # Daily steps
    try:
        stats = client.get_stats(yesterday) or {}
        steps = stats.get("totalSteps")
        if steps:
            result["steps_yesterday"] = int(steps)
    except Exception:
        pass

    # VO2 max — from max metrics endpoint
    try:
        max_data = client.get_max_metrics(yesterday)
        vo2 = None
        if isinstance(max_data, list):
            for item in max_data:
                gd = (item.get("generic") or {})
                vo2 = gd.get("vo2MaxPreciseValue") or gd.get("vo2MaxValue")
                if vo2:
                    break
        elif isinstance(max_data, dict):
            vo2 = max_data.get("vo2MaxPreciseValue") or max_data.get("vo2MaxValue")
        if vo2:
            result["vo2_max"] = round(float(vo2), 1)
    except Exception:
        pass

    cache = _load_cache()
    cache[str(chat_id)] = result
    _CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return result


def get_cached(chat_id: int, for_date: str | None = None) -> dict | None:
    """Return cached entry for yesterday (or for_date) if it exists."""
    target = for_date or (date.today() - timedelta(days=1)).isoformat()
    entry = _load_cache().get(str(chat_id))
    return entry if entry and entry.get("date") == target else None


def _load_cache() -> dict:
    if _CACHE.exists():
        try:
            return json.loads(_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}
