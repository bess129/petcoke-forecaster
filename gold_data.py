"""
gold_data.py - Gold prices and gold futures market data (all free, allowed sources).

  * Yahoo Finance (yfinance) COMEX gold futures "GC=F": daily history and a
    live quote (about 15 minutes delayed).
      data/gold_daily.csv   daily close (USD per troy ounce)
      data/gold.csv         monthly average -> used by the forecasting models
  * CFTC Commitments of Traders (US futures regulator), Disaggregated
    Futures-Only report, COMEX gold (code 088691), weekly:
      data/gold_cot.csv     open interest and positions of managed money
                            (hedge funds), producers/merchants and swap dealers
Investing.com is linked and its chart widget is embedded on the page, but no
data is copied from it (its terms do not allow that).

Run:  py gold_data.py     (the daily GitHub Action runs it)
"""

from pathlib import Path
import io
import sys
import zipfile

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(__file__).parent / "data"
DAILY_CSV = DATA_DIR / "gold_daily.csv"
MONTHLY_CSV = DATA_DIR / "gold.csv"
COT_CSV = DATA_DIR / "gold_cot.csv"
TICKER = "GC=F"
COT_CODE = "088691"
COT_URL = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
COT_YEARS = 5


# ------------------------------------------------------------------ prices
def update_prices():
    import yfinance as yf
    df = yf.download(TICKER, start="2005-01-01", interval="1d", progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError("Yahoo Finance returned no gold data")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    out = pd.DataFrame({"date": pd.to_datetime(df.index).tz_localize(None).strftime("%Y-%m-%d"),
                        "open": df["Open"].values, "high": df["High"].values,
                        "low": df["Low"].values, "close": df["Close"].values,
                        "volume": df.get("Volume", pd.Series(np.nan, index=df.index)).values})
    out = out.dropna(subset=["close"])
    if DAILY_CSV.exists():                        # keep any older rows we already have
        old = pd.read_csv(DAILY_CSV)
        out = pd.concat([old[~old["date"].isin(out["date"])], out]).sort_values("date")
    out.to_csv(DAILY_CSV, index=False)
    monthly = (out.assign(date=pd.to_datetime(out["date"])).set_index("date")["close"]
               .resample("MS").mean().dropna())
    this_month = pd.Timestamp.today().normalize().replace(day=1)
    monthly = monthly[monthly.index < this_month]      # only complete months go to the models
    pd.DataFrame({"date": monthly.index.strftime("%Y-%m-%d"), "price": monthly.round(2).values}) \
        .to_csv(MONTHLY_CSV, index=False)
    print(f"Gold: {len(out)} daily prices up to {out['date'].iloc[-1]} "
          f"(last {out['close'].iloc[-1]:.1f} USD/oz); {len(monthly)} months")


def load_daily():
    if not DAILY_CSV.exists():
        return pd.DataFrame(columns=["date", "close"])
    d = pd.read_csv(DAILY_CSV, parse_dates=["date"])
    return d.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def live_quote():
    """Latest price (about 15 min delayed). Falls back to the last daily close."""
    try:
        import yfinance as yf
        intr = yf.download(TICKER, period="5d", interval="5m", progress=False, auto_adjust=False)
        if isinstance(intr.columns, pd.MultiIndex):
            intr.columns = intr.columns.get_level_values(0)
        intr = intr.dropna(subset=["Close"])
        daily = yf.download(TICKER, period="10d", interval="1d", progress=False, auto_adjust=False)
        if isinstance(daily.columns, pd.MultiIndex):
            daily.columns = daily.columns.get_level_values(0)
        if len(intr) and len(daily) >= 2:
            ts = pd.Timestamp(intr.index[-1])
            ts = ts.tz_convert("Asia/Singapore") if ts.tzinfo else ts
            last_day = pd.Timestamp(daily.index[-1]).date()
            prev = float(daily["Close"].iloc[-2] if last_day == ts.date() or len(daily) < 2
                         else daily["Close"].iloc[-1])
            price = float(intr["Close"].iloc[-1])
            return {"price": price, "time": ts, "prev_close": prev, "change": price / prev - 1,
                    "live": True, "source": "Yahoo Finance (COMEX GC=F)"}
    except Exception:
        pass
    d = load_daily()
    if len(d) >= 2:
        return {"price": float(d["close"].iloc[-1]), "time": d["date"].iloc[-1],
                "prev_close": float(d["close"].iloc[-2]),
                "change": float(d["close"].iloc[-1] / d["close"].iloc[-2] - 1),
                "live": False, "source": "Yahoo Finance (COMEX GC=F), daily close"}
    return None


def trend_stats(d, today=None):
    """Plain trend numbers from the daily closes."""
    if d.empty:
        return {}
    s = d.set_index("date")["close"]
    last = float(s.iloc[-1])
    end = s.index[-1]

    def ago(days):
        prev = s[s.index <= end - pd.Timedelta(days=days)]
        return last / float(prev.iloc[-1]) - 1 if len(prev) else np.nan

    ytd_base = s[s.index < pd.Timestamp(end.year, 1, 1)]
    ma50, ma200 = s.rolling(50).mean(), s.rolling(200).mean()
    ret = np.log(s).diff()
    vol20 = float(ret.tail(20).std() * np.sqrt(252))
    vol1y = float(ret.tail(252).std() * np.sqrt(252))
    hi52 = float(s[s.index > end - pd.Timedelta(days=365)].max())
    cross = None
    diff = (ma50 - ma200).dropna()
    if len(diff) > 2:
        sign = np.sign(diff)
        flips = sign[sign != sign.shift()].index[1:]
        if len(flips):
            cross = {"date": flips[-1], "type": "golden" if sign[flips[-1]] > 0 else "death"}
    return {"last": last, "date": end, "d1": ago(1), "w1": ago(7), "m1": ago(30), "m6": ago(182),
            "y1": ago(365), "ytd": last / float(ytd_base.iloc[-1]) - 1 if len(ytd_base) else np.nan,
            "ma50": float(ma50.iloc[-1]), "ma200": float(ma200.iloc[-1]),
            "above200": last > float(ma200.iloc[-1]) if ma200.notna().iloc[-1] else None,
            "vol20": vol20, "vol1y": vol1y, "hi52": hi52, "from_hi": last / hi52 - 1, "cross": cross}


# ------------------------------------------------------------------ CFTC positions
def _col(df, *needles):
    for c in df.columns:
        low = c.lower().replace("__", "_")
        if all(n in low for n in needles):
            return c
    raise KeyError(needles)


def parse_cot(df):
    code = _col(df, "cftc_contract_market_code")
    g = df[df[code].astype(str).str.strip().str.zfill(6) == COT_CODE]
    if g.empty:
        return pd.DataFrame()
    date = _col(df, "report_date_as_yyyy")
    out = pd.DataFrame({
        "date": pd.to_datetime(g[date]).dt.strftime("%Y-%m-%d"),
        "open_interest": pd.to_numeric(g[_col(df, "open_interest_all")], errors="coerce"),
        "mm_long": pd.to_numeric(g[_col(df, "m_money_positions_long_all")], errors="coerce"),
        "mm_short": pd.to_numeric(g[_col(df, "m_money_positions_short_all")], errors="coerce"),
        "pm_long": pd.to_numeric(g[_col(df, "prod_merc_positions_long_all")], errors="coerce"),
        "pm_short": pd.to_numeric(g[_col(df, "prod_merc_positions_short_all")], errors="coerce"),
        "swap_long": pd.to_numeric(g[_col(df, "swap_positions_long_all")], errors="coerce"),
        "swap_short": pd.to_numeric(g[_col(df, "swap_", "short_all")], errors="coerce"),
    })
    out["mm_net"] = out["mm_long"] - out["mm_short"]
    return out


def update_cot():
    import urllib.request
    this_year = pd.Timestamp.today().year
    old = pd.read_csv(COT_CSV) if COT_CSV.exists() else pd.DataFrame()
    have = set(pd.to_datetime(old["date"]).dt.year) if not old.empty else set()
    years = [y for y in range(this_year - COT_YEARS + 1, this_year + 1) if y not in have or y == this_year]
    frames = [old[~pd.to_datetime(old["date"]).dt.year.isin(years)]] if not old.empty else []
    for y in years:
        try:
            req = urllib.request.Request(COT_URL.format(year=y), headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=60).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                name = [n for n in z.namelist() if n.lower().endswith((".txt", ".csv"))][0]
                df = pd.read_csv(z.open(name), low_memory=False)
            part = parse_cot(df)
            frames.append(part)
            print(f"  CFTC {y}: {len(part)} weekly gold reports")
        except Exception as e:
            print(f"  CFTC {y} failed: {type(e).__name__}: {e}")
    if frames:
        out = pd.concat(frames).drop_duplicates("date", keep="last").sort_values("date")
        out.to_csv(COT_CSV, index=False)
        print(f"Gold positions: {len(out)} weeks up to {out['date'].iloc[-1]}")


def load_cot():
    if not COT_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(COT_CSV, parse_dates=["date"]).sort_values("date").reset_index(drop=True)


def cot_stats(cot):
    """Managed-money (hedge fund) positioning in plain numbers."""
    if cot.empty or len(cot) < 10:
        return {}
    last, prev = cot.iloc[-1], cot.iloc[-2]
    three_y = cot[cot["date"] > last["date"] - pd.Timedelta(days=3 * 365)]
    pct = float((three_y["mm_net"] < last["mm_net"]).mean())
    ago4 = cot.iloc[-5] if len(cot) >= 5 else cot.iloc[0]
    return {"date": last["date"], "net": float(last["mm_net"]), "net_prev": float(prev["mm_net"]),
            "net_chg_w": float(last["mm_net"] - prev["mm_net"]),
            "net_chg_4w": float(last["mm_net"] - ago4["mm_net"]),
            "pct_3y": pct, "oi": float(last["open_interest"]),
            "oi_chg_4w": float(last["open_interest"] / ago4["open_interest"] - 1),
            "long_share": float(last["mm_long"] / (last["mm_long"] + last["mm_short"]))}


def main():
    DATA_DIR.mkdir(exist_ok=True)
    try:
        update_prices()
    except Exception as e:
        print(f"Gold price update failed ({type(e).__name__}: {e}) - keeping existing data.")
    try:
        update_cot()
    except Exception as e:
        print(f"CFTC update failed ({type(e).__name__}: {e}) - keeping existing data.")


if __name__ == "__main__":
    main()
