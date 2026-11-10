"""
forecaster.py - Baseline 30-day price forecaster (Step 3 of the sprint brief).

What it does:
  1. Loads a historical price CSV from /data (columns: date, price).
  2. Pulls live proxy prices from yfinance (Crude Oil CL=F for Pet Coke,
     Copper HG=F + Aluminum ALI=F for minor metals).
  3. Resamples everything to a clean monthly series.
  4. Fits Prophet (falls back to LightGBM, then to a simple trend model)
     and returns a forecast DataFrame with: ds, yhat, yhat_lower, yhat_upper.

Run it on its own to test:
    py forecaster.py
"""

import sys
# Windows logs/terminals default to cp1252, which can't print some characters
# Claude or news titles use (e.g. non-breaking hyphens). Use UTF-8 instead.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from pathlib import Path
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).parent / "data"

# Which CSV file and which live proxy tickers belong to each commodity.
COMMODITIES = {
    # Real physical prices: US export unit values from UN Comtrade (USD/tonne)
    "Pet Coke (fuel-grade)": {"file": "petcoke_fuel.csv",     "proxies": ["CL=F"]},
    "Pet Coke (calcined)":   {"file": "petcoke_calcined.csv", "proxies": ["CL=F"]},
    # Broad BLS producer price index (FRED WPU058103) - longer history, but a proxy
    "Pet Coke (PPI index)":  {"file": "petcoke.csv",          "proxies": ["CL=F"]},
    "Cobalt":    {"file": "cobalt.csv",    "proxies": ["HG=F", "ALI=F"]},
    "Molybdenum":  {"file": "molybdenum.csv",  "proxies": ["HG=F"]},     # by-product of copper mining
    "Lithium":     {"file": "lithium.csv",     "proxies": ["HG=F"]},
    "Rare Earths": {"file": "rare_earths.csv", "proxies": ["HG=F"]},
    "Tin":         {"file": "tin.csv",         "proxies": ["HG=F"]},
    "Antimony":  {"file": "antimony.csv",  "proxies": ["HG=F", "ALI=F"]},
    # UN Comtrade groups gallium, germanium, indium etc. under one HS code (811292)
    "Gallium/Germanium (HS 811292)": {"file": "gallium.csv", "proxies": ["HG=F", "ALI=F"]},
}

HISTORY_YEARS = 10   # only train on the most recent N years (old regimes add noise)


# --------------------------------------------------------------------------
# 1. Load historical CSV
# --------------------------------------------------------------------------
def _parse_dates(col):
    """Handle 2024-01-31 (FRED/ISO) as well as 31/01/2024 (Excel in Singapore
    often re-saves dates as day/month/year)."""
    col = col.astype(str).str.strip()
    iso = pd.to_datetime(col, format="ISO8601", errors="coerce")
    if iso.notna().mean() > 0.9:
        return iso
    return pd.to_datetime(col, format="mixed", dayfirst=True, errors="coerce")


def load_history(csv_path) -> pd.DataFrame:
    """Read a CSV with columns date, price. Returns monthly DataFrame [ds, y]."""
    df = pd.read_csv(csv_path)

    # Be forgiving about column names: use 'date'/'price' if present,
    # otherwise assume first column = date, second = price.
    cols = {c.lower().strip(): c for c in df.columns}
    date_col = cols.get("date", df.columns[0])
    price_col = cols.get("price", df.columns[1])

    out = pd.DataFrame({
        "ds": _parse_dates(df[date_col]),
        # FRED marks missing values with "." -> coerce turns those into NaN
        "y": pd.to_numeric(df[price_col], errors="coerce"),
    }).dropna()

    if out.empty:
        raise ValueError(f"No usable rows in {csv_path}. Check the date/price columns.")

    # Clean monthly frequency (month start), averaging if data is daily/weekly.
    monthly = (out.set_index("ds")["y"].resample("MS").mean()
               .interpolate(limit=3).dropna().reset_index())

    cutoff = monthly["ds"].max() - pd.DateOffset(years=HISTORY_YEARS)
    return monthly[monthly["ds"] >= cutoff].reset_index(drop=True)


# --------------------------------------------------------------------------
# 2. Live proxy prices from yfinance
# --------------------------------------------------------------------------
def load_proxies(tickers, start) -> pd.DataFrame:
    """Daily close prices for each ticker. Returns DataFrame indexed by date.
    Tickers that fail to download are skipped (never crashes the app)."""
    try:
        import yfinance as yf
    except ImportError:
        print("  yfinance not installed - running without proxies")
        return pd.DataFrame()

    frames = {}
    for t in tickers:
        close = None
        try:
            raw = yf.download(t, start=start, progress=False, auto_adjust=True)
            if not raw.empty:
                close = raw["Close"]
                if isinstance(close, pd.DataFrame):   # newer yfinance returns 2-D
                    close = close.iloc[:, 0]
                close.index = pd.to_datetime(close.index).tz_localize(None)
                print(f"  {t}: {len(close)} daily prices from Yahoo, last = {close.iloc[-1]:.2f}")
        except Exception as e:
            print(f"  {t}: Yahoo download failed ({e})")

        if close is None or close.empty:
            close = _load_fred_backup(t, start)

        if close is not None and not close.empty:
            frames[t] = close
        else:
            print(f"  {t}: no data from Yahoo or FRED, skipping")

    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames).ffill().dropna()


# Backup source when Yahoo Finance is blocked or rate-limited.
FRED_BACKUP = {
    "CL=F":  "DCOILWTICO",   # WTI crude oil, daily
    "HG=F":  "PCOPPUSDM",    # copper, monthly (IMF via FRED)
    "ALI=F": "PALUMUSDM",    # aluminum, monthly (IMF via FRED)
}


def _load_fred_backup(ticker, start):
    series_id = FRED_BACKUP.get(ticker)
    if not series_id:
        return None
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        df = pd.read_csv(url)
        s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values,
                      index=pd.to_datetime(df.iloc[:, 0])).dropna()
        s = s[s.index >= start]
        print(f"  {ticker}: {len(s)} prices from FRED backup ({series_id}), last = {s.iloc[-1]:.2f}")
        return s
    except Exception as e:
        print(f"  {ticker}: FRED backup failed ({e})")
        return None


def _regressor_name(ticker):
    return "proxy_" + ticker.replace("=", "_").replace("-", "_")


def _proxy_values(proxies_daily, dates):
    """Look up proxy values for the given dates. Dates past the last known
    proxy price are filled with the latest price (carry-forward)."""
    if proxies_daily.empty:
        return pd.DataFrame(index=range(len(dates)))
    full = proxies_daily.reindex(proxies_daily.index.union(dates)).ffill().bfill()
    vals = full.loc[dates]
    vals.columns = [_regressor_name(c) for c in vals.columns]
    return vals.reset_index(drop=True)


# --------------------------------------------------------------------------
# 3a. Prophet model
# --------------------------------------------------------------------------
def _forecast_prophet(hist, proxies_daily, future_dates):
    from prophet import Prophet
    import logging
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

    # A yearly pattern needs several years of data to learn. With less than
    # 4 years, a one-off event (e.g. an export ban spike) would be mistaken for
    # "seasonality" and wrongly repeated, so switch it off for short series.
    long_history = len(hist) >= 48
    m = Prophet(yearly_seasonality=long_history, weekly_seasonality=False,
                daily_seasonality=False, interval_width=0.80,
                changepoint_prior_scale=0.05 if long_history else 0.02)

    train = hist.copy()
    if not proxies_daily.empty:
        # Monthly average proxy price lined up with each training month
        monthly_proxy = proxies_daily.resample("MS").mean()
        train = train.merge(monthly_proxy, left_on="ds", right_index=True, how="left")
        train = train.rename(columns={c: _regressor_name(c) for c in proxies_daily.columns})
        reg_cols = [_regressor_name(c) for c in proxies_daily.columns]
        train[reg_cols] = train[reg_cols].ffill().bfill()
        for c in reg_cols:
            m.add_regressor(c, standardize=True)

    m.fit(train)

    # Prophet learned from MONTHLY points. Asking it for daily values makes the
    # yearly-seasonality curve zig-zag between months, so predict at month
    # starts only, then draw a smooth line through them for the daily dates.
    anchors = pd.date_range(hist["ds"].max(),
                            future_dates.max() + pd.offsets.MonthBegin(1), freq="MS")
    future = pd.DataFrame({"ds": anchors})
    future = pd.concat([future, _proxy_values(proxies_daily, anchors)], axis=1)
    fc = m.predict(future).set_index("ds")[["yhat", "yhat_lower", "yhat_upper"]]

    # Anchor the forecast to the last real price, so the line starts where
    # history ends instead of jumping to Prophet's fitted value.
    fc = fc + (hist["y"].iloc[-1] - fc["yhat"].iloc[0])
    daily = _to_daily(fc, future_dates)

    # Uncertainty should start small at the last real price and grow with time
    # (Prophet gives a near-constant band on monthly data). Full width at ~90 days.
    days_ahead = (daily["ds"] - hist["ds"].max()).dt.days.values
    scale = np.sqrt(np.clip(days_ahead / 90.0, 0.05, 1.0))
    daily["yhat_upper"] = daily["yhat"] + (daily["yhat_upper"] - daily["yhat"]) * scale
    daily["yhat_lower"] = daily["yhat"] - (daily["yhat"] - daily["yhat_lower"]) * scale
    return daily


def _to_daily(monthly_fc, future_dates):
    """Interpolate a month-start forecast onto daily dates."""
    daily = (monthly_fc.reindex(monthly_fc.index.union(future_dates))
             .interpolate(method="time").loc[future_dates])
    return daily.rename_axis("ds").reset_index()


# --------------------------------------------------------------------------
# 3b. LightGBM fallback (lag features, recursive monthly forecast)
# --------------------------------------------------------------------------
def _forecast_lightgbm(hist, proxies_daily, future_dates):
    import lightgbm as lgb

    s = hist.set_index("ds")["y"]
    lags = [1, 2, 3, 6, 12]

    def features(series):
        X = pd.DataFrame({f"lag{l}": series.shift(l) for l in lags})
        X["month"] = X.index.month
        return X

    X = features(s)
    data = X.assign(y=s).dropna()
    model = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05,
                              min_child_samples=5, verbose=-1)
    model.fit(data.drop(columns="y"), data["y"])

    resid_std = float(np.std(data["y"] - model.predict(data.drop(columns="y"))))

    # Recursive forecast month by month until we cover the last future date
    extended = s.copy()
    month = s.index.max()
    while month < future_dates.max():
        month = month + pd.offsets.MonthBegin(1)
        extended.loc[month] = np.nan
        x_new = features(extended).iloc[[-1]]
        extended.loc[month] = model.predict(x_new)[0]

    # Interpolate monthly points to the requested daily dates
    yhat = (extended.reindex(extended.index.union(future_dates))
            .interpolate(method="time").loc[future_dates].values)
    steps = np.arange(1, len(future_dates) + 1) / 30.0     # months ahead
    band = 1.28 * resid_std * np.sqrt(np.maximum(steps, 1))  # ~80% interval
    return pd.DataFrame({"ds": future_dates, "yhat": yhat,
                         "yhat_lower": yhat - band, "yhat_upper": yhat + band})


# --------------------------------------------------------------------------
# 3c. Last-resort fallback: recent trend + volatility band
# --------------------------------------------------------------------------
def _forecast_naive(hist, future_dates):
    recent = hist.tail(12)
    last = float(recent["y"].iloc[-1])
    monthly_change = float(recent["y"].diff().mean())
    vol = float(recent["y"].diff().std())
    months_ahead = (future_dates - hist["ds"].max()).days / 30.0
    yhat = last + monthly_change * months_ahead
    band = 1.28 * vol * np.sqrt(np.maximum(months_ahead, 1))
    return pd.DataFrame({"ds": future_dates, "yhat": yhat,
                         "yhat_lower": yhat - band, "yhat_upper": yhat + band})


# --------------------------------------------------------------------------
# 3d. Short histories (< 4 years): damped trend
# --------------------------------------------------------------------------
MIN_MONTHS_FOR_ML = 48


# phi chosen by backtest (3/6/9/12-month holdouts on antimony and gallium):
# 0.3 beat 0.5 and 0.8 - with ~2.5 years of data, recent trends rarely persist.
def _forecast_damped(hist, future_dates, phi=0.3):
    """Start at the latest price, follow the last 6 months' average monthly
    change, but let that trend fade (each month counts phi x the previous).
    Band = recent month-to-month volatility, growing with time."""
    s = hist["y"]
    last = float(s.iloc[-1])
    trend = float(s.tail(7).diff().mean())          # avg monthly change, last 6 months
    vol = float(s.tail(13).diff().std())             # last 12 month-to-month moves
    m = (future_dates - hist["ds"].max()).days.values / 30.0
    # sum of trend * phi^k for k = 1..m  (continuous approximation)
    cum = trend * phi * (1 - phi ** m) / (1 - phi)
    yhat = last + cum
    band = 1.28 * vol * np.sqrt(np.clip(m, 0.05, None))
    return pd.DataFrame({"ds": future_dates, "yhat": yhat,
                         "yhat_lower": np.maximum(yhat - band, 0), "yhat_upper": yhat + band})


# --------------------------------------------------------------------------
# 3e. Extra simple candidates for model selection
# --------------------------------------------------------------------------
def _forecast_nochange(hist, future_dates):
    """Last known price carried forward (the benchmark every model must beat)."""
    last = float(hist["y"].iloc[-1])
    return pd.DataFrame({"ds": future_dates, "yhat": last})


def _forecast_ridge(hist, future_dates, lags=6):
    """Momentum model: next month's % change predicted from the last `lags`
    monthly % changes (ridge regression on log returns), applied recursively."""
    from sklearn.linear_model import Ridge

    lp = np.log(hist["y"].astype(float).values)
    r = np.diff(lp)
    if len(r) < lags + 12:
        raise ValueError("not enough history for ridge model")
    X = np.array([r[i - lags:i] for i in range(lags, len(r))])
    model = Ridge(alpha=1.0).fit(X, r[lags:])

    months = (future_dates - hist["ds"].max()).days.values / 30.44
    n_steps = int(np.ceil(months.max())) + 1
    buf, level, path = list(r[-lags:]), lp[-1], [lp[-1]]
    for _ in range(n_steps):
        step = float(model.predict(np.array(buf[-lags:]).reshape(1, -1))[0])
        level += step
        buf.append(step)
        path.append(level)
    yhat = np.exp(np.interp(months, np.arange(len(path)), path))
    return pd.DataFrame({"ds": future_dates, "yhat": yhat})


# --------------------------------------------------------------------------
# 4. Model selection by walk-forward validation
# --------------------------------------------------------------------------
MODELS = {
    "No-change":        lambda h, p, d: _forecast_nochange(h, d),
    "Damped trend":     lambda h, p, d: _forecast_damped(h, d),
    "Momentum (ridge)": lambda h, p, d: _forecast_ridge(h, d),
    "Prophet":          lambda h, p, d: _forecast_prophet(h, p, d),
    "LightGBM":         lambda h, p, d: _forecast_lightgbm(h, p, d),
}
ML_MODELS = {"Prophet", "LightGBM"}
CV_FOLDS = 8          # how many past forecast origins to test
BAND_QUANTILE = 0.80  # shaded range covers 80% of past errors


def _candidate_names(n_months):
    names = ["No-change", "Damped trend", "Momentum (ridge)"]
    if n_months >= MIN_MONTHS_FOR_ML:
        names += ["Prophet", "LightGBM"]
    return names


def _predict(name, hist, proxies, dates):
    """Point forecast of `name` at `dates` -> numpy array."""
    if name.startswith("Blend: "):
        parts = name[len("Blend: "):].split(" + ")
        return np.mean([_predict(p, hist, proxies, dates) for p in parts], axis=0)
    return np.asarray(MODELS[name](hist, proxies, dates)["yhat"], dtype=float)


def walk_forward(hist, proxies, horizon, names, folds=CV_FOLDS):
    """Pretend it is an earlier month, forecast the next `horizon` months with
    only the data known then, compare with what happened. Repeated `folds`
    times, ending with the most recent months.
    Returns DataFrame: model, origin, h, actual, pred, ape."""
    rows, n = [], len(hist)
    for k in range(folds, 0, -1):
        cut = n - horizon - (k - 1)
        if cut < 18:
            continue
        train, test = hist.iloc[:cut], hist.iloc[cut:cut + horizon]
        origin = train["ds"].max()
        # proxies only up to the end of the origin month (no peeking ahead)
        prox = (proxies.loc[: origin + pd.offsets.MonthEnd(0)]
                if not proxies.empty else proxies)
        dates = pd.DatetimeIndex(test["ds"])
        for name in names:
            try:
                pred = _predict(name, train, prox, dates)
            except Exception as e:
                print(f"  CV {name} failed at {origin:%Y-%m}: {type(e).__name__}")
                continue
            for h, (a, p_) in enumerate(zip(test["y"].values, pred), start=1):
                rows.append({"model": name, "origin": origin, "h": h,
                             "actual": a, "pred": p_, "ape": abs(p_ - a) / abs(a)})
    return pd.DataFrame(rows)


def select_model(cv):
    """Rank models by average % error; also test a blend of the best two.
    Returns (chosen_name, leaderboard DataFrame, cv incl. blend rows)."""
    counts = cv.groupby("model").size()
    full = counts[counts == counts.max()].index          # only models that never failed
    cv = cv[cv["model"].isin(full)]
    board = cv.groupby("model")["ape"].mean().sort_values()
    if len(board) >= 2:
        a, b = board.index[:2]
        pa = cv[cv["model"] == a].set_index(["origin", "h"])
        pb = cv[cv["model"] == b].set_index(["origin", "h"])
        blend = pa[["actual"]].copy()
        blend["pred"] = (pa["pred"] + pb["pred"]) / 2
        blend["ape"] = (blend["pred"] - blend["actual"]).abs() / blend["actual"].abs()
        blend["model"] = f"Blend: {a} + {b}"
        cv = pd.concat([cv, blend.reset_index()], ignore_index=True)
        board = cv.groupby("model")["ape"].mean().sort_values()
    leaderboard = (board.mul(100).round(1).rename("avg_error_pct").reset_index())
    return board.index[0], leaderboard, cv


def _bands_from_errors(cv, name, yhat, months_ahead, horizon):
    """80% range from the chosen model's own past % errors.
    Errors are pooled across all folds and horizons after scaling by
    sqrt(months ahead), so even rare big misses (e.g. a 24% one-month drop)
    widen the range. Range grows with sqrt(months ahead)."""
    errs = cv[cv["model"] == name]
    scaled = errs["ape"] / np.sqrt(errs["h"])
    q = float(scaled.quantile(BAND_QUANTILE)) if len(scaled) else 0.1
    width = q * np.sqrt(np.clip(months_ahead, 0, None))
    return yhat * (1 - width), yhat * (1 + width)


def forecast_and_evaluate(commodity="Pet Coke (fuel-grade)", horizon_days=30, use_proxies=True):
    """
    Full pipeline used by the dashboard.
    Returns (history_df, forecast_df, info)
      forecast_df: ds, yhat, yhat_lower, yhat_upper (daily, from last data to today+30)
      info: chosen model, leaderboard, cv rows, horizon
    """
    cfg = COMMODITIES[commodity]
    hist = load_history(DATA_DIR / cfg["file"])
    last_date = hist["ds"].max()
    end = max(pd.Timestamp.today().normalize(), last_date) + pd.Timedelta(days=horizon_days)
    future_dates = pd.date_range(last_date + pd.Timedelta(days=1), end, freq="D")
    horizon = int(np.clip(np.ceil((end - last_date).days / 30.44), 3, 6))

    proxies = pd.DataFrame()
    if use_proxies and len(hist) >= MIN_MONTHS_FOR_ML:
        start = (hist["ds"].min() - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
        proxies = load_proxies(cfg["proxies"], start)

    names = _candidate_names(len(hist))
    cv = walk_forward(hist, proxies, horizon, names)
    chosen, leaderboard, cv = select_model(cv)
    print(f"  Model selection ({len(names)} candidates, {CV_FOLDS} folds, "
          f"{horizon}-month horizon): chose {chosen}")

    # Forecast at month starts, then draw a smooth daily line through them.
    anchors = pd.date_range(last_date + pd.offsets.MonthBegin(1),
                            end + pd.offsets.MonthBegin(1), freq="MS")
    yhat_m = _predict(chosen, hist, proxies, anchors)
    months_m = (anchors - last_date).days.values / 30.44
    lo_m, hi_m = _bands_from_errors(cv, chosen, yhat_m, months_m, horizon)
    monthly = pd.DataFrame({"yhat": np.r_[hist["y"].iloc[-1], yhat_m],
                            "yhat_lower": np.r_[hist["y"].iloc[-1], lo_m],
                            "yhat_upper": np.r_[hist["y"].iloc[-1], hi_m]},
                           index=pd.DatetimeIndex(np.r_[[last_date], anchors]))
    fc = _to_daily(monthly, future_dates)
    fc["yhat_lower"] = fc["yhat_lower"].clip(lower=0)

    info = {"model": chosen, "leaderboard": leaderboard, "cv": cv,
            "horizon": horizon, "folds": CV_FOLDS, "n_months": len(hist)}
    return hist, fc, info


def run_forecast(commodity="Pet Coke (fuel-grade)", horizon_days=30, use_proxies=True):
    """Backwards-compatible wrapper: (history, forecast, model_name)."""
    hist, fc, info = forecast_and_evaluate(commodity, horizon_days, use_proxies)
    return hist, fc, info["model"]


if __name__ == "__main__":
    commodity = "Pet Coke (PPI index)"
    print(f"Running forecast for {commodity}...")
    hist, fc, model = run_forecast(commodity)

    print(f"\nModel used: {model}")
    print(f"History: {len(hist)} months, {hist['ds'].min():%Y-%m} to {hist['ds'].max():%Y-%m}")
    print(f"Last actual price: {hist['y'].iloc[-1]:,.2f}")
    print("\nForecast (every 7th day):")
    print(fc.iloc[::7].round(2).to_string(index=False))
    print(f"\nFinal day: {fc['ds'].iloc[-1]:%Y-%m-%d}  yhat = {fc['yhat'].iloc[-1]:,.2f}")

    out = DATA_DIR / "forecast_petcoke.csv"
    fc.to_csv(out, index=False)
    print(f"\nSaved to {out}")
