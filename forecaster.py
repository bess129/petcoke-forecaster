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
# 4. Main entry point
# --------------------------------------------------------------------------
def run_forecast(commodity="Pet Coke (fuel-grade)", horizon_days=30, use_proxies=True):
    """
    Returns (history_df, forecast_df, model_used)
      history_df : ds, y               (monthly historical prices)
      forecast_df: ds, yhat, yhat_lower, yhat_upper  (daily forecast)

    The forecast runs from the last historical data point to today + horizon_days,
    so the chart always reaches 30 days into the real future even if the CSV
    is a few months old (FRED/EIA publish with a lag).
    """
    cfg = COMMODITIES[commodity]
    hist = load_history(DATA_DIR / cfg["file"])
    last_date = hist["ds"].max()

    end = max(pd.Timestamp.today().normalize(), last_date) + pd.Timedelta(days=horizon_days)
    future_dates = pd.date_range(last_date + pd.Timedelta(days=1), end, freq="D")

    proxies = pd.DataFrame()
    if use_proxies and len(hist) >= MIN_MONTHS_FOR_ML:
        start = (hist["ds"].min() - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
        proxies = load_proxies(cfg["proxies"], start)

    fc, name = _fit_predict(hist, proxies, future_dates)
    return hist, fc, name


def _fit_predict(hist, proxies, future_dates):
    """Pick the model and forecast future_dates. Returns (forecast_df, model_name)."""
    # Too little history for Prophet/LightGBM to learn anything reliable
    # (one-off events get mistaken for patterns) -> transparent damped trend.
    if len(hist) < MIN_MONTHS_FOR_ML:
        print(f"  Only {len(hist)} months of data - using damped trend instead of Prophet")
        return _forecast_damped(hist, future_dates), "Damped trend (short history)"

    for name, fn in [("Prophet", lambda: _forecast_prophet(hist, proxies, future_dates)),
                     ("LightGBM", lambda: _forecast_lightgbm(hist, proxies, future_dates))]:
        try:
            return fn(), name
        except Exception as e:
            print(f"  {name} failed: {type(e).__name__}: {e}")
    return _forecast_naive(hist, future_dates), "Naive trend"


# --------------------------------------------------------------------------
# 5. Backtest: how accurate would the model have been?
# --------------------------------------------------------------------------
def backtest(commodity="Pet Coke (fuel-grade)", holdout_months=6, use_proxies=True):
    """
    Hide the last `holdout_months` of real prices, forecast them using only the
    earlier data, and compare with what actually happened.

    Benchmark = "no-change" forecast (last known price carried forward). A model
    is only useful if it beats this simple guess.

    Returns (table, summary)
      table  : ds, actual, model, naive
      summary: dict with model_mape, naive_mape, model_name, beats_naive
    """
    cfg = COMMODITIES[commodity]
    full = load_history(DATA_DIR / cfg["file"])
    if len(full) < holdout_months + 12:
        raise ValueError("Not enough history for a backtest.")

    train, test = full.iloc[:-holdout_months], full.iloc[-holdout_months:]
    proxies = pd.DataFrame()
    if use_proxies and len(train) >= MIN_MONTHS_FOR_ML:
        start = (full["ds"].min() - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
        proxies = load_proxies(cfg["proxies"], start)

    test_dates = pd.DatetimeIndex(test["ds"])
    fc, name = _fit_predict(train, proxies, test_dates)

    table = pd.DataFrame({
        "ds": test_dates,
        "actual": test["y"].values,
        "model": fc["yhat"].values,
        "naive": float(train["y"].iloc[-1]),
    })

    def mape(pred):
        return float((abs(pred - table["actual"]) / table["actual"].abs()).mean() * 100)

    summary = {
        "model_name": name,
        "holdout_months": holdout_months,
        "train_end": train["ds"].max(),
        "model_mape": mape(table["model"]),
        "naive_mape": mape(table["naive"]),
    }
    summary["beats_naive"] = summary["model_mape"] < summary["naive_mape"]
    return table, summary


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
