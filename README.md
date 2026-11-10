# Minor Metals & Pet Coke Early-Warning Forecaster

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
| Pet Coke | `data/petcoke.csv` | FRED series WPU058103 → Download → CSV |
| Cobalt | `data/cobalt.csv` | IMF Primary Commodity Prices Excel (column `PCOBA`, USD/ton) |
| Antimony | `data/antimony.csv` | UN Comtrade, HS 811010, USD/kg (see below) |
| Gallium/Germanium | `data/gallium.csv` | UN Comtrade, HS 811292, USD/kg (mixed group: gallium, germanium, indium, niobium, vanadium) |

**UN Comtrade (antimony, gallium/germanium):** on comtradeplus.un.org → Trade statistics, set
Monthly, commodity codes 811010 + 811292, Reporter **USA**, Partner **World**, Flow **Import**,
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

- Pet Coke uses a broad BLS producer price index as a proxy, not a true pet coke spot price (those are behind Platts/Argus paywalls).
- Price data lags by 1–3 months; the forecast is extended to today + 30 days.
- The shock overlay is a transparent rule, not a trained model.
- Commodities with less than 4 years of data (antimony, gallium/germanium) use a damped
  trend instead of Prophet: with ~2.5 years, Prophet mistook the 2025 China export-ban
  spike for a yearly pattern and predicted it would repeat.
- HS 811292 mixes several metals, so the "Gallium/Germanium" price is noisy and its
  forecast range is very wide.
