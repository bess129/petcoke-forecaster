"""
build_forecasts.py - Pre-calculate every commodity's forecast and model test,
so the dashboard opens instantly (the daily GitHub Action runs this).

    py build_forecasts.py

Writes data/forecast_cache.pkl. The app uses a cached forecast only when the
price file has not changed since and the cache is less than 36 hours old;
otherwise it calculates live.
"""

from datetime import datetime, timezone
import hashlib
import pickle
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from forecaster import COMMODITIES, DATA_DIR, forecast_and_evaluate

CACHE = DATA_DIR / "forecast_cache.pkl"
MAX_AGE_HOURS = 36


def file_hash(path):
    return hashlib.md5(path.read_bytes()).hexdigest() if path.exists() else None


def build():
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "items": {}}
    for c, cfg in COMMODITIES.items():
        path = DATA_DIR / cfg["file"]
        if not path.exists():
            continue
        t0 = time.time()
        try:
            hist, fc, info = forecast_and_evaluate(c)
        except Exception as e:
            print(f"  {c}: FAILED ({type(e).__name__}: {e})")
            continue
        out["items"][c] = {"hash": file_hash(path), "result": (hist, fc, info)}
        print(f"  {c}: {info['model']} ({time.time() - t0:.0f}s)")
    with open(CACHE, "wb") as f:
        pickle.dump(out, f)
    print(f"Saved {len(out['items'])} forecasts to {CACHE}")


def load_cached(commodity):
    """Return (hist, fc, info) from the cache if still valid, else None."""
    try:
        with open(CACHE, "rb") as f:
            cache = pickle.load(f)
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(cache["generated_at"])).total_seconds() / 3600
        item = cache["items"].get(commodity)
        if item is None or age > MAX_AGE_HOURS:
            return None
        if item["hash"] != file_hash(DATA_DIR / COMMODITIES[commodity]["file"]):
            return None
        return item["result"]
    except Exception:
        return None


if __name__ == "__main__":
    print("Pre-calculating forecasts for all commodities...")
    build()
