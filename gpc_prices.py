"""
gpc_prices.py - Green petroleum coke (GPC) prices by country, sulphur and
vanadium level, from public and (optionally) subscription sources.

Data file: data/gpc_prices.csv - one row per published price, with its date,
basis, grade and source link. Added to by:
  * price_watch.py   (weekly automatic search of public price reports)
  * import_argus.py  (future Argus subscription exports / company quotes)
plus the UN Comtrade US export series (data/petcoke_fuel.csv).

Rules this module enforces:
  * Every price keeps its own date; age is shown, nothing old is called current.
  * Prices are only compared within the same basis (FOB / CFR / customs avg).
  * Forecasts are only made for recent prices (<= 62 days old).
"""

from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
PRICES_CSV = DATA_DIR / "gpc_prices.csv"
COMTRADE_CSV = DATA_DIR / "petcoke_fuel.csv"

COLUMNS = ["date", "series_id", "origin", "market", "basis", "sulfur_pct", "vanadium_ppm",
           "hgi", "price", "currency", "unit", "price_type", "source", "source_url",
           "published", "retrieved", "method", "note"]

# Grade definitions used by this system (stated on the page).
LOW_S, HIGH_S = 3.0, 5.0         # <3% low, 3-5% medium, >=5% high sulphur
LOW_V = 500                      # <=500 ppm = low vanadium
FRESH_DAYS, RECENT_DAYS, FORECAST_MAX_AGE = 7, 31, 62


def load_prices():
    df = pd.read_csv(PRICES_CSV) if PRICES_CSV.exists() else pd.DataFrame(columns=COLUMNS)
    # UN Comtrade: US export average (all uncalcined grades), monthly
    if COMTRADE_CSV.exists():
        c = pd.read_csv(COMTRADE_CSV)
        c = pd.DataFrame({
            "date": c.iloc[:, 0], "series_id": "COMTRADE_US_EXPORT", "origin": "USA",
            "market": "US exports (customs average)", "basis": "FOB customs avg",
            "price": pd.to_numeric(c.iloc[:, 1], errors="coerce"), "currency": "USD", "unit": "t",
            "price_type": "customs average", "source": "UN Comtrade (HS 271311, US exports to World)",
            "source_url": "https://comtradeplus.un.org", "method": "stated",
            "note": "Monthly average of all uncalcined grades exported from the USA"})
        df = pd.concat([df, c], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["sulfur_pct"] = pd.to_numeric(df["sulfur_pct"], errors="coerce")
    df["vanadium_ppm"] = pd.to_numeric(df["vanadium_ppm"], errors="coerce")
    df = df.dropna(subset=["date", "price", "series_id"])
    df = df.drop_duplicates(subset=["series_id", "date", "price"], keep="last")
    # monthly customs averages describe the whole month: their "as of" date is month end
    monthly = df["basis"].astype(str).str.contains("customs", case=False)
    df["as_of"] = np.where(monthly, df["date"] + pd.offsets.MonthEnd(0), df["date"])
    df["as_of"] = pd.to_datetime(df["as_of"])
    df["monthly"] = monthly
    return df.sort_values(["series_id", "date"]).reset_index(drop=True)


def sulfur_grade(s):
    if pd.isna(s):
        return "mixed"
    return "low" if s < LOW_S else "medium" if s < HIGH_S else "high"


def vanadium_grade(v):
    if pd.isna(v):
        return "unpublished"
    return "low" if v <= LOW_V else "high"


def freshness(as_of, today=None):
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    days = int((today - pd.Timestamp(as_of).normalize()).days)
    level = "fresh" if days <= FRESH_DAYS else "recent" if days <= RECENT_DAYS else "stale"
    return days, level


def latest_table(df, today=None):
    """Most recent price of every series, with grade, age and change vs previous."""
    rows = []
    for sid, g in df.groupby("series_id"):
        g = g.sort_values("date")
        last = g.iloc[-1]
        prev = g.iloc[-2] if len(g) > 1 else None
        days, level = freshness(last["as_of"], today)
        rows.append({**last.to_dict(), "n_points": len(g), "age_days": days, "freshness": level,
                     "sulfur_grade": sulfur_grade(last["sulfur_pct"]),
                     "vanadium_grade": vanadium_grade(last["vanadium_ppm"]),
                     "prev_price": prev["price"] if prev is not None else np.nan,
                     "prev_date": prev["date"] if prev is not None else pd.NaT,
                     "change_pct": (last["price"] / prev["price"] - 1) if prev is not None else np.nan})
    return pd.DataFrame(rows).sort_values(["basis", "price"]).reset_index(drop=True)


def basis_group(basis):
    b = str(basis).lower()
    if "customs" in b:
        return "customs"
    if "cfr" in b or "cif" in b or "del" in b:
        return "cfr"
    if "fob" in b:
        return "fob"
    return "other"


def series_forecast(latest_row, benchmark, today=None, horizon_days=30):
    """Forecast for one series = its latest price moved by the benchmark GPC
    model's expected % change, with the benchmark's % range widened for the
    price's age. Only for prices <= FORECAST_MAX_AGE days old.

    benchmark: dict(change_pct, band_pct) from the main GPC model.
    Returns dict or None (with reason)."""
    days = latest_row["age_days"]
    if days > FORECAST_MAX_AGE:
        return {"ok": False, "reason": "stale", "age_days": days}
    total_days = days + horizon_days
    scale = np.sqrt(total_days / max(horizon_days, 1))
    chg = benchmark.get("change_pct", 0.0) * (total_days / max(benchmark.get("days", total_days), 1))
    band = benchmark.get("band_pct", 0.15) * scale
    p = latest_row["price"]
    fc = p * (1 + chg)
    return {"ok": True, "forecast": fc, "lo": max(fc * (1 - band), 0), "hi": fc * (1 + band),
            "change_pct": chg, "band_pct": band, "age_days": days,
            "reliability": "low" if latest_row["n_points"] < 12 else "medium"}


def benchmark_from(fc, hist, target):
    """% change and % range of the main GPC model at the target date."""
    row = fc.iloc[(fc["ds"] - target).abs().argmin()]
    last = float(hist["y"].iloc[-1])
    days = int((row["ds"] - hist["ds"].iloc[-1]).days)
    return {"change_pct": float(row["yhat"]) / last - 1,
            "band_pct": float(row["yhat_upper"] - row["yhat"]) / float(row["yhat"]),
            "days": max(days, 1)}
