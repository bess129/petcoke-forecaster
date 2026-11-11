"""
accuracy.py - How accurate have the forecasts been? (supervisor point 1)

Two kinds of evidence:
  1. Back-test ("过去测试"): the walk-forward test already run by forecaster.py.
     The system pretends it is an earlier month, forecasts with only the data
     known then, and compares with what really happened (8 test dates).
  2. Live track record ("实际追踪记录"): every time new price data arrives,
     build_forecasts.py saves the forecast for the next month in
     data/forecast_log.csv. When that month's real price arrives, it is compared.
     This is the strictest test: these forecasts were made before the price existed.
"""

import numpy as np
import pandas as pd

from forecaster import DATA_DIR, BAND_QUANTILE

LOG_CSV = DATA_DIR / "forecast_log.csv"
LOG_COLUMNS = ["made_on", "commodity", "base_month", "base_price", "target_month",
               "forecast", "lo", "hi", "model"]
MOVE = 0.02          # a move smaller than 2% counts as "flat" for the direction test


def _origin_price(hist):
    return hist.set_index("ds")["y"]


def _coverage(cv):
    """Share of actuals inside the 80% range, each test date judged with a range
    built only from the OTHER test dates (so the test is not graded by itself)."""
    hits = []
    for o, g in cv.groupby("origin"):
        other = cv[cv["origin"] != o]
        if other.empty:
            continue
        q = float((other["ape"] / np.sqrt(other["h"])).quantile(BAND_QUANTILE))
        w = q * np.sqrt(g["h"])
        hits += list((g["actual"] >= g["pred"] * (1 - w)) & (g["actual"] <= g["pred"] * (1 + w)))
    return float(np.mean(hits)) if hits else np.nan


def metrics(hist, info):
    """Plain-language accuracy numbers for one commodity's chosen model."""
    cv, model = info["cv"], info["model"]
    mine = cv[cv["model"] == model].copy()
    base = cv[cv["model"] == "No-change"]
    y0 = _origin_price(hist)
    mine["y0"] = mine["origin"].map(y0)
    by_h = mine.groupby("h")["ape"].mean()
    err = float(mine["ape"].mean())
    err_nc = float(base["ape"].mean()) if not base.empty else np.nan

    moved = mine[(mine["actual"] / mine["y0"] - 1).abs() >= MOVE]
    pred_dir = np.sign(moved["pred"] - moved["y0"])
    real_dir = np.sign(moved["actual"] - moved["y0"])
    predicts_direction = (mine["pred"] - mine["y0"]).abs().gt(mine["y0"] * 0.002).any()
    hit = float((pred_dir == real_dir).mean()) if len(moved) and predicts_direction else np.nan

    worst = mine.loc[mine["ape"].idxmax()] if len(mine) else None
    return {
        "model": model, "err": err, "err_1m": float(by_h.get(1, np.nan)),
        "err_3m": float(by_h.get(3, np.nan)), "by_h": by_h,
        "err_nochange": err_nc,
        "vs_nochange": (err_nc - err) / err_nc if err_nc and err_nc == err_nc else np.nan,
        "direction_hit": hit, "direction_n": int(len(moved)),
        "coverage": _coverage(mine), "n_tests": int(mine["origin"].nunique()),
        "n_points": int(len(mine)), "horizon": int(info.get("horizon", mine["h"].max())),
        "first_test": mine["origin"].min(), "last_test": mine["origin"].max(),
        "worst_err": float(worst["ape"]) if worst is not None else np.nan,
        "worst_month": (worst["origin"] + pd.DateOffset(months=int(worst["h"]))) if worst is not None else None,
    }


def rating(err_1m):
    """Same thresholds as the rest of the dashboard (avg % error)."""
    e = err_1m * 100
    return "high" if e < 6 else "mid" if e < 12 else "low"


def test_paths(hist, info):
    """Past test forecasts as paths starting from the last known price, for a chart."""
    cv = info["cv"]
    mine = cv[cv["model"] == info["model"]]
    y0 = _origin_price(hist)
    paths = []
    for o, g in mine.groupby("origin"):
        g = g.sort_values("h")
        months = [o] + [o + pd.DateOffset(months=int(h)) for h in g["h"]]
        paths.append({"origin": o, "ds": months, "y": [float(y0.get(o, np.nan))] + list(g["pred"])})
    return paths


# ------------------------------------------------------------------ live log
def log_forecast(commodity, hist, fc, info, today=None):
    """Save the next-month forecast once per new data month (called daily)."""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    base = hist["ds"].max()
    target = base + pd.offsets.MonthBegin(1)
    row = fc.iloc[(fc["ds"] - target).abs().argmin()]
    log = pd.read_csv(LOG_CSV) if LOG_CSV.exists() else pd.DataFrame(columns=LOG_COLUMNS)
    exists = ((log["commodity"] == commodity) & (log["base_month"] == base.strftime("%Y-%m-%d"))).any()
    if exists:
        return False
    new = {"made_on": today.strftime("%Y-%m-%d"), "commodity": commodity,
           "base_month": base.strftime("%Y-%m-%d"), "base_price": float(hist["y"].iloc[-1]),
           "target_month": target.strftime("%Y-%m-%d"), "forecast": round(float(row["yhat"]), 4),
           "lo": round(float(row["yhat_lower"]), 4), "hi": round(float(row["yhat_upper"]), 4),
           "model": info["model"]}
    pd.concat([log, pd.DataFrame([new])], ignore_index=True)[LOG_COLUMNS].to_csv(LOG_CSV, index=False)
    return True


def track_record(histories):
    """Logged forecasts joined with the real price once it is known."""
    if not LOG_CSV.exists():
        return pd.DataFrame(columns=LOG_COLUMNS + ["actual", "error", "inside", "status"])
    log = pd.read_csv(LOG_CSV, parse_dates=["made_on", "base_month", "target_month"])
    actual = []
    for _, r in log.iterrows():
        h = histories.get(r["commodity"])
        s = h.set_index("ds")["y"] if h is not None else pd.Series(dtype=float)
        actual.append(float(s.get(r["target_month"], np.nan)))
    log["actual"] = actual
    log["error"] = (log["forecast"] - log["actual"]).abs() / log["actual"]
    log["nochange_error"] = (log["base_price"] - log["actual"]).abs() / log["actual"]
    log["inside"] = (log["actual"] >= log["lo"]) & (log["actual"] <= log["hi"])
    log["status"] = np.where(log["actual"].isna(), "waiting", "done")
    return log.sort_values("made_on", ascending=False).reset_index(drop=True)
