# Minor Metals & Pet Coke Early-Warning Forecaster

Interface: **Simplified Chinese by default**, switch to English in the sidebar (`i18n.py` holds all text).

Main pet coke focus: **green petroleum coke (GPC)**, the un-calcined coke (HS 271311) used as kiln/boiler fuel and as calciner feed.

A Streamlit app that forecasts Pet Coke and minor-metal prices 30 days ahead,
scans Google News for early supply-chain "weak signals", scores them with
Claude, and bends the forecast when high-urgency signals appear.

## How it works

| File | Job |
|---|---|
| `forecaster.py` | Loads `data/<commodity>.csv`, adds a live proxy (crude oil `CL=F` for Pet Coke; copper `HG=F` and aluminum `ALI=F` for metals; FRED is used automatically if Yahoo Finance is blocked), fits Prophet (falls back to LightGBM, then a trend model), returns `ds, yhat, yhat_lower, yhat_upper`. |
| `scraper.py` | Google News RSS searches for weak-signal keywords (export quotas, smelter outages, coker turnarounds…). Saves `data/news.csv`. |
| `signal_detector.py` | Sends headlines to Claude (`claude-sonnet-5-5`) to get category, price direction (-1..+1), urgency (1–5) and a one-line reason. Caches results in `data/signals.csv` so headlines are never scored twice. |
| `shock.py` | Regime-shift overlay: urgency-weighted direction of urgency ≥ 4 signals from the last 7 days. If the net score exceeds 0.2, the forecast is shifted by up to 15% over 30 days and the cone widened. The manual slider adds on top. |
| `comtrade_to_csv.py` | Converts UN Comtrade downloads into `antimony.csv` and `gallium.csv` (unit-value method). |
| Backtest | In the app, open **"How accurate is this forecast?"**: the last 6 months are hidden, forecast from earlier data, and compared with reality and with a no-change guess (error shown as MAPE). |
| `forecaster.py` model selection | Each commodity: 3–5 candidate models (no-change, damped trend, momentum/ridge, Prophet, LightGBM) plus a blend of the best two are tested 8 times on past data (walk-forward). The lowest average % error wins. The shaded 80% range comes from that model's real past misses. |
| `outlook.py` | Daily Claude "supply-shortage outlook" per commodity (upside / neutral / downside risk, drivers, what to watch) saved to `data/outlook.json`. |
| `imf_to_csv.py` | Extracts cobalt, molybdenum, lithium, rare earths and tin from the IMF Excel file. |
| `build_forecasts.py` | Pre-calculates every forecast daily (`data/forecast_cache.pkl`) so the dashboard opens instantly; the app falls back to calculating live if the cache is old or the price file changed. |
| `i18n.py` | All dashboard text in Chinese and English, commodity names, units. |
| `app.py` | Streamlit dashboard: KPI cards, forecast chart with adjusted cone, Weak Signal Radar table. |
| `refresh.bat` | Runs scraper → signal detector → forecaster (for the daily scheduled task). |

## 1. Install

Requires Python 3.12. On Windows use `py` (not `python`):

```
cd C:\Users\Chen\petcoke_forecaster
py -m pip install -r requirements.txt
```

## 2. Data files (`/data`)

Each commodity reads one CSV with two columns, **date** and **price**
(the first two columns are used if the headers are named differently):

| Commodity | File | Suggested source |
|---|---|---|
| **Green Pet Coke (GPC)** (main focus) | `data/petcoke_fuel.csv` | UN Comtrade, HS 271311 (petroleum coke, not calcined), US exports to World, USD/tonne |
| Calcined Pet Coke (CPC) (reference) | `data/petcoke_calcined.csv` | UN Comtrade, HS 271312, US exports to World, USD/tonne |
| Pet Coke PPI index (broad) (reference) | `data/petcoke.csv` | FRED series WPU058103 → Download → CSV (broad index, longer history) |
| Cobalt, Molybdenum, Lithium, Rare Earths, Tin | `data/cobalt.csv`, `molybdenum.csv`, `lithium.csv`, `rare_earths.csv`, `tin.csv` | IMF Primary Commodity Prices Excel: save as `data/external-data.xlsx`, run `py imf_to_csv.py` |
| Antimony | `data/antimony.csv` | UN Comtrade, HS 811010, USD/kg (see below) |
| Gallium/Germanium | `data/gallium.csv` | UN Comtrade, HS 811292, USD/kg (mixed group: gallium, germanium, indium, niobium, vanadium) |

**UN Comtrade (antimony, gallium/germanium):** on comtradeplus.un.org → Trade statistics, set
Monthly, commodity codes 811010 + 811292 (Flow **Import**) or 271311 + 271312 (Flow **Export**), Reporter **USA**, Partner **World**,
Breakdown **Classic**, and 12 months per download (larger requests are refused on free accounts).
Save each `UNComtrade-*.csv` into `/data`, then run `py comtrade_to_csv.py`. It computes
price = value / net weight per month and skips months under 1,000 kg.

To update: download the newer file and overwrite the old one with the same
name. Dates may be `2026-09-01` or `01/09/2026`. Daily, weekly or monthly data
all work (it is averaged to monthly). A commodity with no CSV shows a
"No data yet" message in the app.

## 3. Anthropic API key

Create a key at https://console.anthropic.com/settings/keys (and add credit
under Billing). Save it in PowerShell, pasting the key at the prompt:

```
$k = Read-Host "Paste key"
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", $k.Trim(), "User")
```

Then close and reopen the terminal. Check with
`$env:ANTHROPIC_API_KEY.Substring(0,7)` → should print `sk-ant-`.
Never put the key in code files or share it.

Cost: scoring ~100 headlines is roughly US$0.10–0.15; reruns only pay for new headlines.

### Online (Streamlit Community Cloud) secrets

In the app's **Settings → Secrets**:

```
ANTHROPIC_API_KEY = "sk-ant-..."
ADMIN_PASSWORD = "choose-a-password"
```

`ADMIN_PASSWORD` locks the "Refresh news & Claude signals" button online, because each
refresh spends API credit. Locally (no secrets file) the button needs no password.

## Supply Risk Outlook tab

Ranks every commodity by supply-shortage pressure in the last 30 days of news:
urgency-weighted price direction of supply-type headlines (outages, export rules,
host-metal cuts, refinery turnarounds, logistics) × a confidence factor that grows
with the number of headlines naming the metal. Only metal-specific headlines can
mark a metal as upside risk; general critical-minerals headlines add a small
background amount (at most ~0.06). Shown with 3- and 12-month price change and
Claude's written outlook. On the Forecast tab, news headlines appear as triangles on
the price chart.

## 4. Run

```
py scraper.py            # fetch news            -> data/news.csv
py signal_detector.py    # score with Claude     -> data/signals.csv
py -m streamlit run app.py
```

The dashboard opens at http://localhost:8501. The sidebar button
"Refresh news & Claude signals" does the first two steps from inside the app.

## 5. Daily automatic refresh (Windows Task Scheduler)

Runs every morning at 07:30 and logs to `data/refresh_log.txt`:

```
schtasks /create /tn "PetCokeForecasterRefresh" /tr "C:\Users\Chen\petcoke_forecaster\refresh.bat" /sc daily /st 07:30
```

To check: open **Task Scheduler** → Task Scheduler Library → PetCokeForecasterRefresh
(right-click → Run to test). To remove:
`schtasks /delete /tn "PetCokeForecasterRefresh" /f`

(macOS/Linux equivalent: `crontab -e` and add
`30 7 * * * cd /path/to/project && python scraper.py && python signal_detector.py && python forecaster.py`)

## Troubleshooting

| Message | Fix |
|---|---|
| `'pip' is not recognized` | Use `py -m pip …` |
| `CL=F: possibly delisted` | Yahoo Finance is blocked/rate-limited; the FRED backup is used automatically. |
| `invalid x-api-key` (401) | The saved key is wrong. Redo step 3 and reopen the terminal. |
| `streamlit` not recognized | Use `py -m streamlit run app.py` |
| Regime Status always "Normal" | Expected when no urgency ≥ 4 signals in the last 7 days. Use the slider to demo a shock. |

## Limitations

- Pet coke prices are US export unit values (value ÷ weight), a monthly average clearing price, not a daily spot quote (those are behind Platts/Argus paywalls). The FRED PPI index is kept for its longer history.
- Price data lags by 1–3 months; the forecast is extended to today + 30 days.
- The shock overlay is a transparent rule, not a trained model.
- Commodities with less than 4 years of data (antimony, gallium/germanium) use a damped
  trend instead of Prophet: with ~2.5 years, Prophet mistook the 2025 China export-ban
  spike for a yearly pattern and predicted it would repeat.
- News is matched to each metal by name (cobalt/Congo, antimony, gallium/germanium/indium);
  headlines naming no specific metal (e.g. "critical minerals export controls") apply to all metals.
- Walk-forward tests show the models often do not beat a no-change guess; the app then uses no-change.
  Illiquid metals move on one-off policy events that past prices cannot predict.
- HS 811292 mixes several metals, so the "Gallium/Germanium" price is noisy and its
  forecast range is very wide.

## Pet coke prices by origin, sulphur and vanadium (石油焦价格 page)

| File | What it does |
|---|---|
| `data/gpc_prices.csv` | One row per published GPC price: date, origin, market, basis (FOB/CFR/customs avg), sulphur %, vanadium ppm, HGI, price, source and link |
| `gpc_prices.py` | Loads prices (+ UN Comtrade US export series), grades sulphur (<3% low, 3–5% medium, ≥5% high) and vanadium (≤500 ppm low), labels age (🟢 ≤7 days, 🟡 ≤31 days, 🔴 out of date), forecasts only prices ≤62 days old |
| `price_watch.py` | Every 3 days (in the GitHub Action) Claude searches the web for newly published prices; only numbers written in the source, with link and quote, are kept (marked "auto-extracted") |
| `import_argus.py` | Imports Argus Excel/CSV exports or company quotes put in `data/argus/`; reads sulphur, HGI, basis and market from the price name |
| `data/gpc_prices_template.csv` | Template for company/supplier quotes |
| `petcoke_tab.py` | The dashboard page |

Public sources used: Argus free sample report and free news, SMM (China customs import averages by origin),
Energy Transit Intelligence weekly, UN Comtrade. Limitations: full daily Argus/Platts prices need a subscription;
no free source prices coke by vanadium; no free verifiable Saudi FOB price was found; customs averages are 1–2 months late.

## Forecast Accuracy page (预测准确度)

`accuracy.py` turns the walk-forward back-test (8 past test dates per commodity) into plain numbers:
average error overall / 1 month / 3 months ahead, comparison with the no-change benchmark, direction hit
rate (moves > 2% only), and 80%-range coverage (each test date judged with a range built from the other
dates). It also keeps a **live track record**: `build_forecasts.py` saves each next-month forecast to
`data/forecast_log.csv` when new data arrives, and the page compares it once the real price is published.
`accuracy_tab.py` is the page.

## Price Shifts & Events page (价格大变动与事件)

| File | What it does |
|---|---|
| `shifts.py` | Finds each commodity's biggest 3-month moves since 2018 (≥15% and ≥2.5× its usual move; customs series smoothed), summarises warning patterns, and checks whether similar news is appearing now |
| `explain_shifts.py` | For each new shift: Google News from 2 months before to 3 weeks after (incl. environment/weather searches); Claude names the cause and the early-warning headlines, citing only given headline ids (checked). Cached in `data/shift_events.json` (first run ~US$1-3, then only new shifts) |
| `shifts_tab.py` | The dashboard page |

New news category **Environment & Weather (环保政策与天气)**: environmental inspections, pollution/emission
limits, permit suspensions, hurricanes, floods, drought, typhoons, power rationing. Used in daily scoring
(`signal_detector.py`), supply risk (`shock.py`), daily searches (`scraper.py`) and the shift analysis.

## Investing.com links and widgets

`investing_links.py` links each commodity to its Investing.com page (cn.investing.com in Chinese,
www.investing.com in English) and embeds Investing.com's official free widgets (technical chart for
crude oil / Brent / copper, and the economic calendar) on the Overview page, with the required credit.
Investing.com has no official API and its terms forbid copying its data, so no prices are downloaded
from it. It has no page for molybdenum, antimony, gallium/germanium or pet coke; related markets are linked.
