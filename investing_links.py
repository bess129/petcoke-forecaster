"""
investing_links.py - Links to Investing.com and its official free widgets.

Investing.com has no official data API, and its terms do not allow copying its
data, so the dashboard does NOT download prices from it. Instead it:
  * links each commodity to its Investing.com page (Chinese site cn.investing.com
    when the dashboard is in Chinese, www.investing.com in English)
  * embeds Investing.com's own free widgets (technical chart, economic calendar)
    with the "provided by Investing.com" credit they ask for.
Investing.com has no page for molybdenum, antimony, gallium/germanium or pet
coke itself; for pet coke the markets that drive its price are linked.
"""

import streamlit as st
import streamlit.components.v1 as components

from i18n import t, is_zh

# (path on investing.com, Chinese label, English label)
PAGES = {
    "crude":     ("commodities/crude-oil", "WTI 原油期货", "WTI crude oil futures"),
    "brent":     ("commodities/brent-oil", "布伦特原油期货", "Brent oil futures"),
    "coal":      ("commodities/newcastle-coal-futures", "纽卡斯尔煤炭期货", "Newcastle coal futures"),
    "aluminium": ("commodities/aluminum", "铝期货", "Aluminium futures"),
    "cobalt":    ("commodities/cobalt", "钴期货", "Cobalt futures"),
    "lithium":   ("commodities/lithium-carbonate-99-min-china-futures", "碳酸锂期货（中国）",
                  "Lithium carbonate futures (China)"),
    "tin":       ("commodities/tin", "锡期货", "Tin futures"),
    "remx":      ("etfs/remx", "稀土与战略金属基金 REMX", "Rare earth & strategic metals ETF (REMX)"),
    "copper":    ("commodities/copper", "铜期货", "Copper futures"),
    "zinc":      ("commodities/zinc-futures", "锌期货", "Zinc futures"),
    "metals":    ("commodities/metals", "金属行情总览", "All metals prices"),
    "energy":    ("commodities/energy", "能源行情总览", "All energy prices"),
}

# which pages to show for each commodity ("related" = drivers, not the same product)
COMMODITY_PAGES = {
    "Green Pet Coke (GPC)":          {"related": ["crude", "coal", "brent"]},
    "Calcined Pet Coke (CPC)":       {"related": ["aluminium", "crude"]},
    "Pet Coke PPI index (broad)":    {"related": ["crude", "coal"]},
    "Cobalt":                        {"direct": ["cobalt"]},
    "Lithium":                       {"direct": ["lithium"]},
    "Rare Earths":                   {"related": ["remx"]},
    "Tin":                           {"direct": ["tin"]},
    "Molybdenum":                    {"related": ["metals"]},
    "Antimony":                      {"related": ["metals"]},
    "Gallium/Germanium (HS 811292)": {"related": ["zinc", "aluminium"]},   # by-products of zinc / alumina
}

# Investing.com widget settings: instrument ids for the chart widget, language ids
CHART_PAIRS = {"crude": 8849, "brent": 8833, "copper": 8831}


def base_url():
    return "https://cn.investing.com/" if is_zh() else "https://www.investing.com/"


def url(key):
    return base_url() + PAGES[key][0]


def label(key):
    return PAGES[key][1] if is_zh() else PAGES[key][2]


def links_md(commodity):
    """One line of markdown links for a commodity, or '' if nothing fits."""
    cfg = COMMODITY_PAGES.get(commodity, {})
    parts = []
    if cfg.get("direct"):
        parts.append(t("inv_direct") + " " + " · ".join(f"[{label(k)} ↗]({url(k)})" for k in cfg["direct"]))
    if cfg.get("related"):
        parts.append(t("inv_related") + " " + " · ".join(f"[{label(k)} ↗]({url(k)})" for k in cfg["related"]))
    return "　".join(parts)


def show_links(commodity):
    md = links_md(commodity)
    if md:
        # plain markdown (links inside raw HTML blocks would not be clickable)
        st.markdown(f"📈 **Investing.com**{'：' if is_zh() else ': '}{md}")
        if not COMMODITY_PAGES.get(commodity, {}).get("direct"):
            st.caption(t("inv_no_direct"))


def _lang_id():
    return 6 if is_zh() else 1          # 6 = Simplified Chinese, 1 = English


def chart_widget(key="crude", height=420):
    pair = CHART_PAIRS[key]
    src = (f"https://ssltvc.investing.com/?pair_ID={pair}&height={height}&width=100%25&interval=86400"
           f"&plotStyle=area&domain_ID={_lang_id()}&lang_ID={_lang_id()}&timezone_ID=28")
    components.html(
        f"<iframe src='{src}' width='100%' height='{height}' frameborder='0' allowtransparency='true' "
        f"marginwidth='0' marginheight='0'></iframe>"
        f"<div style='font-family:Arial,sans-serif;font-size:11px;color:#333'>{t('inv_chart_credit')} "
        f"<a href='{base_url()}' rel='nofollow' target='_blank' style='color:#06529D;font-weight:bold'>"
        f"Investing.com</a></div>", height=height + 30)
    st.markdown(t("inv_fallback", url=url(key), name=label(key)))


def calendar_widget(height=470):
    # countries: USA, China, India, Euro area, Japan, Australia, Canada, Saudi Arabia, Singapore
    countries = "5,37,14,72,35,25,6,52,36"
    src = ("https://sslecal2.investing.com?columns=exc_flags,exc_currency,exc_importance,exc_actual,"
           f"exc_forecast,exc_previous&features=datepicker,timezone&countries={countries}"
           f"&calType=week&timeZone=28&lang={_lang_id()}")
    components.html(
        f"<iframe src='{src}' width='100%' height='{height}' frameborder='0' allowtransparency='true' "
        f"marginwidth='0' marginheight='0'></iframe>"
        f"<div style='font-family:Arial,sans-serif;font-size:11px;color:#333'>{t('inv_cal_credit')} "
        f"<a href='{base_url()}' rel='nofollow' target='_blank' style='color:#06529D;font-weight:bold'>"
        f"Investing.com</a></div>", height=height + 30)
    st.markdown(t("inv_fallback", url=base_url() + "economic-calendar/", name=t("inv_cal_name")))


def market_section():
    """Overview section: all Investing.com links + the two widgets."""
    st.subheader(t("inv_title"))
    st.caption(t("inv_caption"))
    from forecaster import COMMODITIES
    from i18n import name_of
    lines = [f"- **{name_of(c)}**：{links_md(c)}" if is_zh() else f"- **{name_of(c)}**: {links_md(c)}"
             for c in COMMODITIES if links_md(c)]
    st.markdown("\n".join(lines))
    tab_chart, tab_cal = st.tabs([t("inv_tab_chart"), t("inv_tab_cal")])
    with tab_chart:
        choice = st.radio(t("inv_pick"), list(CHART_PAIRS), format_func=label, horizontal=True, key="inv_chart")
        chart_widget(choice)
    with tab_cal:
        st.caption(t("inv_cal_caption"))
        calendar_widget()
