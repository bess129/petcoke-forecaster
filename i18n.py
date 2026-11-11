"""
i18n.py - Chinese (default) and English text for the dashboard.

Use:  from i18n import t, name_of, unit_of
      t("overview_title")            -> text in the current language
      t("forecast_line", price=1.2)  -> with {placeholders} filled in
"""

LANG = {"code": "zh"}          # fallback outside the dashboard (scripts, tests)


def _session():
    """Each visitor has their own language (stored in their Streamlit session)."""
    try:
        import streamlit as st
        return st.session_state
    except Exception:
        return None


def set_lang(code):
    code = "en" if code == "en" else "zh"
    ss = _session()
    try:
        ss["lang"] = code
    except Exception:
        LANG["code"] = code


def is_zh():
    ss = _session()
    try:
        return ss.get("lang", "zh") == "zh"
    except Exception:
        return LANG["code"] == "zh"


def fmt_date(ts):
    return f"{ts.month}月{ts.day}日" if is_zh() else f"{ts.day} {ts:%b}"


def fmt_month(ts):
    return f"{ts.year}年{ts.month}月" if is_zh() else f"{ts:%b %Y}"


def fmt_price(v, ref=None):
    """Format a price; `ref` (e.g. the latest price) fixes the decimals so a
    range like 71.3–132.7 uses the same precision on both ends."""
    if v != v:          # NaN
        return "–"
    r = abs(ref if ref is not None else v)
    if r >= 1000:
        return f"{v:,.0f}"
    if r >= 100:
        return f"{v:,.1f}"
    return f"{v:,.2f}"


def t(key, **kw):
    entry = TEXT.get(key)
    if entry is None:
        return key
    s = entry[0] if is_zh() else entry[1]
    return s.format(**kw) if kw else s


def pick(zh, en):
    """Choose between a Chinese and English value (falls back if one is empty)."""
    if is_zh():
        return zh if isinstance(zh, str) and zh.strip() else en
    return en if isinstance(en, str) and en.strip() else zh


# ---------------------------------------------------------------- names
COMMODITY_NAMES = {
    "Green Pet Coke (GPC)":          ("生石油焦（GPC）", "Green Pet Coke (GPC)"),
    "Cobalt":                        ("钴", "Cobalt"),
    "Molybdenum":                    ("钼", "Molybdenum"),
    "Lithium":                       ("锂", "Lithium"),
    "Rare Earths":                   ("稀土", "Rare Earths"),
    "Tin":                           ("锡", "Tin"),
    "Antimony":                      ("锑", "Antimony"),
    "Gallium/Germanium (HS 811292)": ("镓/锗等（HS 811292）", "Gallium/Germanium (HS 811292)"),
    "Calcined Pet Coke (CPC)":       ("煅烧石油焦（CPC）", "Calcined Pet Coke (CPC)"),
    "Pet Coke PPI index (broad)":    ("石油焦价格指数（宽口径）", "Pet Coke PPI index (broad)"),
}

UNITS = {  # (short unit zh, short unit en, long description zh, long description en)
    "Green Pet Coke (GPC)":          ("美元/吨", "USD/t", "美国出口单价（HS 271311，联合国 Comtrade）",
                                      "US export unit value (HS 271311, UN Comtrade)"),
    "Calcined Pet Coke (CPC)":       ("美元/吨", "USD/t", "美国出口单价（HS 271312，联合国 Comtrade）",
                                      "US export unit value (HS 271312, UN Comtrade)"),
    "Pet Coke PPI index (broad)":    ("指数", "index", "美国生产者价格指数（1984年12月=100，FRED）",
                                      "US producer price index (Dec 1984 = 100, FRED)"),
    "Cobalt":                        ("美元/吨", "USD/t", "LME 钴 99.8%（国际货币基金组织）", "LME cobalt 99.8% (IMF)"),
    "Molybdenum":                    ("美元/吨", "USD/t", "焙烧钼精矿（国际货币基金组织）", "Roasted molybdenum concentrate (IMF)"),
    "Lithium":                       ("美元/吨", "USD/t", "电池级金属锂（国际货币基金组织）", "Battery-grade lithium metal (IMF)"),
    "Rare Earths":                   ("美元/吨", "USD/t", "稀土碳酸盐 REO 42–45%（国际货币基金组织）",
                                      "Rare earth carbonate REO 42-45% (IMF)"),
    "Tin":                           ("美元/吨", "USD/t", "LME 锡（国际货币基金组织）", "LME tin (IMF)"),
    "Antimony":                      ("美元/千克", "USD/kg", "美国进口单价（HS 811010，联合国 Comtrade）",
                                      "US import unit value (HS 811010, UN Comtrade)"),
    "Gallium/Germanium (HS 811292)": ("美元/千克", "USD/kg", "美国进口单价（HS 811292，镓锗铟等混合）",
                                      "US import unit value (HS 811292, mixed Ga/Ge/In)"),
}


def name_of(commodity):
    zh, en = COMMODITY_NAMES.get(commodity, (commodity, commodity))
    return zh if is_zh() else en


def unit_of(commodity, long=False):
    u = UNITS.get(commodity)
    if not u:
        return ""
    if long:
        return u[2] if is_zh() else u[3]
    return u[0] if is_zh() else u[1]


MODEL_NAMES = {
    "No-change":        ("保持不变（以最新价格为预测）", "No-change (latest price held flat)"),
    "Damped trend":     ("减弱趋势", "Damped trend"),
    "Momentum (ridge)": ("动量模型", "Momentum (ridge)"),
    "Prophet":          ("Prophet 时间序列模型", "Prophet"),
    "LightGBM":         ("LightGBM 机器学习模型", "LightGBM"),
}


def model_name(m):
    if m.startswith("Blend: "):
        parts = m[len("Blend: "):].split(" + ")
        joined = " + ".join(model_name(p) for p in parts)
        return ("组合：" if is_zh() else "Blend: ") + joined
    zh, en = MODEL_NAMES.get(m, (m, m))
    return zh if is_zh() else en


CATEGORY_NAMES = {
    "Supply Outage":       ("供应中断", "Supply Outage"),
    "Export Policy":       ("出口政策", "Export Policy"),
    "Host Metal Cut":      ("主金属减产", "Host Metal Cut"),
    "Refinery Turnaround": ("炼厂检修/停产", "Refinery Turnaround"),
    "Demand Shift":        ("需求变化", "Demand Shift"),
    "Logistics":           ("物流运输", "Logistics"),
    "Noise":               ("无关/噪音", "Noise"),
}


def category_name(c):
    zh, en = CATEGORY_NAMES.get(c, (c, c))
    return zh if is_zh() else en


# ---------------------------------------------------------------- UI text
TEXT = {
    # app frame
    "app_title":        ("小金属与石油焦价格预警系统", "Minor Metals & Pet Coke Early-Warning Forecaster"),
    "lang_label":       ("语言 / Language", "Language / 语言"),
    "tab_overview":     ("总览", "Overview"),
    "tab_forecast":     ("价格预测", "Price Forecast"),
    "tab_risk":         ("供应风险", "Supply Risk"),
    "tab_help":         ("使用说明", "How to use"),
    "commodity":        ("选择品种", "Commodity"),
    "updated":          ("数据更新：{when}", "Updated: {when}"),
    "disclaimer":       ("本系统基于公开数据与模型分析，仅供业务决策参考，不构成投资建议。",
                         "Based on public data and models. For business decision support only, not investment advice."),

    # traffic lights
    "light_red":        ("短缺风险", "Shortage risk"),
    "light_yellow":     ("需关注", "Watch"),
    "light_green":      ("平稳", "Stable"),
    "light_red_why":    ("新闻显示供应趋紧，价格有上涨风险", "News points to tightening supply, upside price risk"),
    "light_yellow_why_up":   ("预测价格明显上升，建议关注", "Forecast shows a notable rise, worth watching"),
    "light_yellow_why_down": ("预测价格明显下跌或新闻偏空，建议关注", "Forecast shows a notable fall or bearish news, worth watching"),
    "light_yellow_why_news": ("有部分供应相关新闻，但证据尚不充分", "Some supply news, but evidence is not yet strong"),
    "light_green_why":  ("暂无明显供应风险信号", "No significant supply-risk signal"),

    # overview
    "overview_intro":   ("以下为各品种的最新价格、未来约30天的预测以及供应风险信号。点击“价格预测”查看详情。",
                         "Latest price, ~30-day forecast and supply-risk signal for each commodity. "
                         "Open “Price Forecast” for details."),
    "overview_alerts":  ("需要关注", "Needs attention"),
    "overview_no_alerts": ("目前没有品种出现明显的供应短缺风险。", "No commodity currently shows a clear shortage risk."),
    "card_latest":      ("最新价格（{month}）", "Latest price ({month})"),
    "card_forecast":    ("预测（{date}）", "Forecast ({date})"),
    "card_range":       ("可能区间 {lo} – {hi}", "Likely range {lo} – {hi}"),
    "card_reliability": ("预测可靠性：{level}", "Reliability: {level}"),
    "rel_high":         ("较高", "high"),
    "rel_mid":          ("中等", "medium"),
    "rel_low":          ("较低", "low"),
    "sentence":         ("预计到{date}价格约为 {fc} {unit}（较最新 {chg}），可能区间 {lo}–{hi}。{why}。",
                         "Expected around {fc} {unit} by {date} ({chg} vs latest), likely range {lo}–{hi}. {why}."),
    "download_excel":   ("下载汇总表（Excel）", "Download summary (Excel)"),
    "download_help":    ("包含所有品种的价格、预测、区间、风险信号与模型测试结果，便于会议使用。",
                         "All commodities: price, forecast, range, risk signal and model test results, for meetings."),
    "computing":        ("正在计算预测（首次约需1–2分钟）……", "Calculating forecasts (first time takes 1–2 minutes)..."),

    # forecast tab
    "kpi_latest":       ("最新价格", "Latest price"),
    "kpi_latest_help":  ("最新公布的月度价格。官方数据通常有1–3个月发布延迟。单位：{unit}",
                         "Most recent published monthly price. Official data is usually published 1–3 months late. Units: {unit}"),
    "kpi_forecast":     ("30天后预测", "Forecast in 30 days"),
    "kpi_forecast_help": ("预测日期为今天起30天后（{date}）。", "Forecast for 30 days from today ({date})."),
    "kpi_vs_latest":    ("较最新 {chg}", "{chg} vs latest"),
    "kpi_range":        ("可能区间（80%）", "Likely range (80%)"),
    "kpi_range_help":   ("根据该模型过去实际预测偏差计算：过去约80%的情况下，实际价格落在此区间内。",
                         "Based on this model's real past errors: about 80% of the time the actual price fell inside this range."),
    "kpi_signal":       ("供应风险信号", "Supply-risk signal"),
    "data_delay":       ("价格数据截至 **{month}**（官方数据有发布延迟），预测从该月延伸至 **{date}**（今天起30天后）。",
                         "Price data runs to **{month}** (official data is published late). The forecast runs from there to **{date}** (30 days from today)."),
    "chart_hist":       ("历史价格", "Historical price"),
    "chart_fc":         ("预测", "Forecast"),
    "chart_fc_adj":     ("预测（含新闻/情景调整）", "Forecast (incl. news/scenario adjustment)"),
    "chart_range":      ("可能区间（80%）", "Likely range (80%)"),
    "chart_last":       ("最新数据", "Last data"),
    "chart_today":      ("今天", "Today"),
    "chart_news_up":    ("新闻：利多（供应趋紧）", "News: bullish (tighter supply)"),
    "chart_news_down":  ("新闻：利空", "News: bearish"),
    "chart_news_flat":  ("新闻：中性", "News: neutral"),
    "chart_caption":    ("三角形为相关新闻（绿色=利多，红色=利空，越大越重要），鼠标悬停可查看标题。",
                         "Triangles are related headlines (green = bullish, red = bearish, bigger = more important). Hover to read."),
    "reliability_title": ("预测可靠性", "Forecast reliability"),
    "reliability_text": ("我们用过去的数据做了 **{folds} 次** 模拟测试（假设身处过去某个月，只用当时已知的数据预测未来 {h} 个月），"
                         "所选方法的平均偏差约 **±{err:.1f}%**。",
                         "We ran **{folds} test forecasts** on past data (pretending to be in an earlier month and using only data known then "
                         "to forecast {h} months ahead). The chosen method missed by about **±{err:.1f}%** on average."),
    "reliability_nochange": ("对该品种而言，复杂模型没有比“保持最新价格不变”更准确，因此系统采用最新价格作为基准预测。"
                             "价格的变化方向主要请参考新闻供应风险信号。",
                             "For this commodity no model beat simply holding the latest price, so the latest price is used as the "
                             "baseline forecast. For the likely direction, rely on the news supply-risk signal."),
    "reliability_model": ("系统自动选用了过去表现最好的方法：**{model}**（比“保持不变”少偏差 {gain:.1f} 个百分点）。",
                          "The system automatically chose the best past performer: **{model}** ({gain:.1f} points more accurate than no-change)."),
    "latest_news":      ("最新相关新闻", "Latest related news"),
    "no_news":          ("近期暂无相关新闻。", "No recent related news."),
    "news_line":        ("{date} · {cat} · 重要性 {u}/5", "{date} · {cat} · importance {u}/5"),

    # advanced
    "advanced":         ("专业分析（模型测试、情景模拟、全部新闻）", "Advanced (model tests, scenarios, all news)"),
    "scenario":         ("情景模拟：假设供应冲击（%）", "Scenario: assume a supply shock (%)"),
    "scenario_help":    ("拖动以模拟未来30天价格受冲击的情况，例如 +10% 表示供应紧张推高价格10%。",
                         "Drag to simulate a shock over the next 30 days, e.g. +10% = tight supply pushes prices up 10%."),
    "regime_alert":     ("**价格异动预警：** 预测在30天内调整 {total}（新闻信号 {sig}，情景设定 {man}）。",
                         "**Regime-shift alert:** forecast adjusted {total} over 30 days (news {sig}, scenario {man})."),
    "model_table":      ("模型测试排名（平均偏差，越低越好）", "Model test ranking (average error, lower is better)"),
    "col_rank":         ("排名", "Rank"),
    "col_model":        ("方法", "Method"),
    "col_err":          ("平均偏差（%）", "Avg error (%)"),
    "col_vs":           ("相对“保持不变”", "vs no-change"),
    "backtest_chart":   ("最近一次模拟测试：{when} 做出的预测 vs 实际价格", "Most recent test: forecast made in {when} vs actual price"),
    "actual_price":     ("实际价格", "Actual price"),
    "all_news":         ("全部相关新闻（Claude 评分）", "All related news (scored by Claude)"),
    "hide_noise":       ("隐藏无关新闻", "Hide unrelated news"),
    "col_urgency":      ("重要性", "Importance"),
    "col_date":         ("日期", "Date"),
    "col_category":     ("类别", "Category"),
    "col_direction":    ("价格影响", "Price impact"),
    "col_headline":     ("标题", "Headline"),
    "col_why":          ("影响说明", "Why it matters"),
    "col_applies":      ("适用范围", "Applies to"),
    "col_source":       ("来源", "Source"),
    "col_link":         ("链接", "Link"),
    "applies_this":     ("该品种", "This metal"),
    "applies_general":  ("整体小金属", "All metals (general)"),
    "open":             ("打开", "open"),
    "no_data":          ("暂无 **{name}** 的数据。", "No data for **{name}** yet."),

    # risk tab
    "risk_intro":       ("**哪些品种可能因供应短缺而涨价？** 根据过去 {days} 天的新闻（停产、出口限制、减产、炼厂检修、物流）计算。"
                         "只有明确提到该品种的新闻才能将其标为“短缺风险”。",
                         "**Which commodities could rise because of supply shortages?** Based on the last {days} days of news "
                         "(outages, export rules, production cuts, refinery turnarounds, logistics). Only headlines that name "
                         "the commodity can mark it as a shortage risk."),
    "risk_chart_title": ("供应短缺风险评分", "Supply-shortage risk score"),
    "risk_axis":        ("风险评分（负=偏空，正=供应趋紧）", "Risk score (negative = bearish, positive = tighter supply)"),
    "col_commodity":    ("品种", "Commodity"),
    "col_signal":       ("风险信号", "Signal"),
    "col_claude":       ("AI 分析师观点", "AI analyst view"),
    "col_score":        ("评分", "Score"),
    "col_headlines":    ("相关供应新闻（直接提及）", "Supply headlines (naming it)"),
    "col_3m":           ("近3个月价格", "Price 3m"),
    "col_12m":          ("近12个月价格", "Price 12m"),
    "col_driver":       ("主要新闻", "Top headline"),
    "claude_title":     ("AI 分析师观点", "AI analyst view"),
    "claude_caption":   ("由 {model} 于 {when}（UTC）根据上述新闻撰写，仅供参考。",
                         "Written by {model} on {when} UTC from the headlines above. For reference only."),
    "claude_none":      ("暂无 AI 分析师观点，每天早上自动更新。", "No AI analyst view yet. It updates automatically every morning."),
    "outlook_up":       ("上涨风险", "Upside risk"),
    "outlook_flat":     ("中性", "Neutral"),
    "outlook_down":     ("下跌风险", "Downside risk"),
    "conf_low":         ("把握较低", "low confidence"),
    "conf_medium":      ("把握中等", "medium confidence"),
    "conf_high":        ("把握较高", "high confidence"),
    "drivers":          ("主要因素", "Drivers"),
    "watch_next":       ("后续关注", "Watch next"),
    "risk_method":      ("评分方法说明", "How the score works"),
    "risk_method_text": ("- **供应压力**：近{days}天供应类新闻（{cats}）对价格影响方向（−1 到 +1）的加权平均，越重要的新闻权重越高。\n"
                         "- **把握程度**：直接提及该品种的新闻越多越可靠（5条及以上为满分）。\n"
                         "- **风险评分** = 供应压力 × 把握程度，另加少量整体小金属新闻的影响（最多约 +0.06，"
                         "因此仅凭整体新闻无法达到 +0.10 的“短缺风险”线）。\n"
                         "- **近3/12个月价格**：判断风险是否已经体现在价格中。\n"
                         "- **AI 分析师观点**：Claude 阅读同样的新闻和价格变化后给出的文字判断，可作为第二意见。",
                         "- **Supply pressure**: importance-weighted average price impact (−1 to +1) of supply-type headlines "
                         "({cats}) in the last {days} days.\n"
                         "- **Confidence** grows with the number of headlines naming the commodity (full at 5).\n"
                         "- **Risk score** = supply pressure × confidence, plus a small amount from general critical-minerals "
                         "news (at most ~+0.06, so general news alone can never reach the +0.10 shortage line).\n"
                         "- **Price 3m / 12m** show whether the risk may already be priced in.\n"
                         "- The **AI analyst view** is Claude's written judgement from the same headlines and prices: a second opinion."),

    # help tab
    "help_md": (
"""
### 如何阅读本系统

**1. 总览** —— 每个品种一张卡片：
- 🔴 **短缺风险**：新闻显示供应趋紧，价格有上涨风险，建议重点关注采购/库存安排。
- 🟡 **需关注**：预测价格变化较大，或有部分供应新闻但证据不足。
- 🟢 **平稳**：暂无明显风险信号。

**2. 价格预测** —— 单个品种的详细图表：
- 蓝线是历史价格，红线是预测，浅红色区域是“可能区间”（过去约80%的情况下实际价格落在其中）。
- **预测可靠性**：系统会用历史数据反复模拟测试多种预测方法，自动选择过去最准确的方法，并告诉您平均偏差有多大。
- 三角形标记是相关新闻，绿色代表利多（供应趋紧），红色代表利空。

**3. 供应风险** —— 所有品种按“供应短缺风险”排序，并附 AI 分析师的文字观点。

### 数据来源
- **生石油焦（GPC）/煅烧石油焦**：联合国 Comtrade 美国出口单价（月度）。
- **钴、钼、锂、稀土、锡**：国际货币基金组织（IMF）初级商品价格（月度）。
- **锑、镓/锗**：联合国 Comtrade 美国进口单价（月度）。
- **新闻**：Google 新闻，每天早上自动收集，由 Claude（AI）评估类别、影响方向和重要性。

### 重要限制
- 官方价格数据通常**延迟1–3个月**发布，因此“最新价格”不是今天的现货价。
- 对价格波动大、受政策影响强的品种，任何模型都难以准确预测涨跌幅度；**新闻风险信号是提前预警的主要依据**。
- 历史测试显示，新闻信号对生石油焦的预测有一定改善（平均偏差 11.6% vs 12.0%），但尚未达到统计显著，系统会持续积累数据重新检验。
- 本系统仅供业务参考，不构成投资建议。
""",
"""
### How to read this dashboard

**1. Overview**: one card per commodity:
- 🔴 **Shortage risk**: news points to tightening supply and upside price risk; review purchasing/inventory plans.
- 🟡 **Watch**: the forecast moves notably, or there is some supply news but the evidence is not yet strong.
- 🟢 **Stable**: no significant risk signal.

**2. Price Forecast**: detailed chart for one commodity:
- Blue = historical price, red = forecast, light-red area = likely range (the actual price fell inside it about 80% of the time in past tests).
- **Reliability**: the system repeatedly tests several forecasting methods on past data, picks the most accurate automatically, and shows its average error.
- Triangles are related headlines: green = bullish (tighter supply), red = bearish.

**3. Supply Risk**: all commodities ranked by shortage risk, with a written AI analyst view.

### Data sources
- **Green / calcined pet coke**: UN Comtrade US export unit values (monthly).
- **Cobalt, molybdenum, lithium, rare earths, tin**: IMF Primary Commodity Prices (monthly).
- **Antimony, gallium/germanium**: UN Comtrade US import unit values (monthly).
- **News**: Google News, collected every morning and scored by Claude (AI) for category, price impact and importance.

### Important limitations
- Official price data is usually published **1–3 months late**, so the "latest price" is not today's spot price.
- For volatile, policy-driven commodities no model predicts the size of moves reliably; **the news risk signal is the main early warning**.
- A historical test showed news slightly improved the GPC forecast (average error 11.6% vs 12.0%), but not yet statistically significant; it will be re-tested as more data accumulates.
- For business decision support only, not investment advice.
"""),

    # sidebar / refresh
    "admin_pw":         ("管理员密码（用于手动更新）", "Admin password (to refresh)"),
    "refresh_btn":      ("立即更新新闻与分析", "Refresh news & analysis now"),
    "refreshing":       ("正在获取新闻并由 AI 分析……", "Fetching news and analysing with AI..."),
    "refresh_ok":       ("已更新。", "Updated."),
    "refresh_fail":     ("更新失败：{err}", "Refresh failed: {err}"),
    "auto_refresh":     ("新闻与分析每天早上 7:17（新加坡时间）自动更新。",
                         "News and analysis update automatically every morning at 7:17 (Singapore time)."),
}
