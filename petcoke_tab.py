"""
petcoke_tab.py - The "石油焦价格 / Pet Coke Prices" page: green petroleum coke
prices by country, sulphur and vanadium level, with date, age and source.
"""

import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from gpc_prices import (load_prices, latest_table, series_forecast, benchmark_from, basis_group,
                        PRICES_CSV, DATA_DIR, LOW_S, HIGH_S, LOW_V, FRESH_DAYS, RECENT_DAYS,
                        FORECAST_MAX_AGE)
from i18n import t, is_zh, fmt_price

TEMPLATE = DATA_DIR / "gpc_prices_template.csv"
FRESH_ICON = {"fresh": "🟢", "recent": "🟡", "stale": "🔴"}
FRESH_COLOR = {"fresh": "#2e8b57", "recent": "#d4a017", "stale": "#b0b0b0"}

COUNTRY_ZH = {"USA": "美国", "Russia": "俄罗斯", "Canada": "加拿大", "Saudi Arabia": "沙特阿拉伯",
              "India": "印度", "Oman": "阿曼", "Colombia": "哥伦比亚", "Taiwan, China": "中国台湾",
              "Brazil": "巴西", "Argentina": "阿根廷", "Romania": "罗马尼亚", "Belgium": "比利时",
              "Spain": "西班牙", "Kazakhstan": "哈萨克斯坦", "Azerbaijan": "阿塞拜疆",
              "Venezuela": "委内瑞拉", "Various": "多个产地", "China": "中国", "Turkey": "土耳其",
              "Kuwait": "科威特", "UAE": "阿联酋", "Mexico": "墨西哥", "Europe": "欧洲"}
MARKET_ZH = {"US Gulf coast": "美湾", "US west coast": "美西", "China": "中国", "India": "印度",
             "West coast India": "印度西海岸", "Turkey": "土耳其",
             "China (import, customs average)": "中国进口（海关均价）",
             "US exports (customs average)": "美国出口（海关均价）", "Saudi Arabia": "沙特阿拉伯",
             "Russia": "俄罗斯", "Canada": "加拿大", "Brazil": "巴西", "Europe": "欧洲"}
BASIS_ZH = {"FOB": "离岸价 FOB", "CFR": "到岸价 CFR", "CIF": "到岸价 CIF",
            "CIF customs avg": "海关进口均价（CIF）", "FOB customs avg": "海关出口均价（FOB）",
            "Delivered": "送到价", "Ex-warehouse": "出库价", "Domestic": "国内价"}


def _zh(value, table):
    if not is_zh() or not isinstance(value, str):
        return value
    return " / ".join(table.get(p.strip(), p.strip()) for p in value.split("/"))


def country(v):
    return _zh(v, COUNTRY_ZH)


def market(v):
    return _zh(v, MARKET_ZH)


def basis_label(v):
    return BASIS_ZH.get(v, v) if is_zh() else v


def sulfur_text(s, grade):
    if grade == "mixed":
        return t("pc_s_mixed")
    return f"{t('pc_s_' + grade)} {s:g}%"


def vanadium_text(v, grade):
    return t("pc_v_unpub") if grade == "unpublished" else f"{t('pc_v_' + grade)} {v:g} ppm"


def age_text(days, level):
    if days < 0:
        return t("pc_age_future")
    if level == "stale":
        months = days // 30
        return t("pc_age_stale_m", m=months) if months >= 1 else t("pc_age_stale_d", d=days)
    return t("pc_age_days", d=days)


def method_text(row):
    m = str(row.get("method", ""))
    pt = str(row.get("price_type", ""))
    if m == "auto-extracted":
        return t("pc_type_auto")
    if m == "imported":
        return t("pc_type_company") if pt == "company quote" else t("pc_type_imported")
    if m == "implied":
        return t("pc_type_implied")
    return {"assessment": t("pc_type_assess"), "customs average": t("pc_type_customs"),
            "market report": t("pc_type_report")}.get(pt, pt)


def short_source(src):
    s = str(src)
    for key, zh, en in [("Argus", "Argus", "Argus"), ("SMM", "SMM（上海有色网）", "SMM"),
                        ("Comtrade", "联合国 Comtrade", "UN Comtrade"),
                        ("ETI", "ETI 能源周报", "ETI weekly")]:
        if key in s:
            return zh if is_zh() else en
    return s[:40]


@st.cache_data(ttl=1800, show_spinner=False)
def _prices(_mtime):
    return load_prices()


def render(forecast_for):
    mtime = PRICES_CSV.stat().st_mtime if PRICES_CSV.exists() else 0
    df = _prices(mtime)
    today = pd.Timestamp.today().normalize()
    if df.empty:
        st.info(t("pc_none"))
        return
    lt = latest_table(df, today)
    n_fresh = int((lt["freshness"] == "fresh").sum())
    n_recent = int((lt["freshness"] == "recent").sum())
    n_stale = int((lt["freshness"] == "stale").sum())
    newest = lt.loc[lt["as_of"].idxmax()]

    st.markdown(t("pc_intro"))
    st.markdown(
        f"<div style='display:flex;flex-wrap:wrap;gap:8px 24px;margin:4px 0 8px;font-size:0.95rem'>"
        f"<span>{t('pc_count', n=len(lt))}</span>"
        f"<span>🟢 {t('pc_cnt_fresh', n=n_fresh, d=FRESH_DAYS)}</span>"
        f"<span>🟡 {t('pc_cnt_recent', n=n_recent, d=RECENT_DAYS)}</span>"
        f"<span>🔴 {t('pc_cnt_stale', n=n_stale)}</span>"
        f"<span>{t('pc_newest', date=newest['date'].strftime('%Y-%m-%d'), src=short_source(newest['source']))}</span>"
        f"</div>", unsafe_allow_html=True)
    if n_fresh == 0:
        st.warning(t("pc_warn_no_fresh"))

    # ------------------------------------------------------------ filters
    groups = {"all": t("pc_g_all"), "fob": t("pc_g_fob"), "cfr": t("pc_g_cfr"),
              "customs": t("pc_g_customs")}
    c1, c2, c3 = st.columns([2, 2, 1.4])
    g = c1.radio(t("pc_basis"), list(groups), format_func=groups.get, horizontal=True, key="pc_basis")
    sgrades = {"all": t("pc_g_all"), "low": t("pc_s_low"), "medium": t("pc_s_medium"),
               "high": t("pc_s_high"), "mixed": t("pc_s_mixed")}
    sg = c2.radio(t("pc_sulfur"), list(sgrades), format_func=sgrades.get, horizontal=True, key="pc_s")
    only_recent = c3.toggle(t("pc_only_recent", d=RECENT_DAYS), value=False, key="pc_recent")

    view = lt.copy()
    view["group"] = view["basis"].map(basis_group)
    if g != "all":
        view = view[view["group"] == g]
    if sg != "all":
        view = view[view["sulfur_grade"] == sg]
    if only_recent:
        view = view[view["freshness"] != "stale"]
    view = view.sort_values(["age_days", "group", "price"])

    # ------------------------------------------------------------ latest table
    st.subheader(t("pc_table_title"))
    if view.empty:
        st.info(t("pc_empty_filter"))
    else:
        table = pd.DataFrame({
            t("pc_c_origin"): view["origin"].map(country),
            t("pc_c_market"): view["market"].map(market),
            t("pc_c_basis"): view["basis"].map(basis_label),
            t("pc_c_sulfur"): [sulfur_text(s, gr) for s, gr in zip(view["sulfur_pct"], view["sulfur_grade"])],
            t("pc_c_vanadium"): [vanadium_text(v, gr) for v, gr in zip(view["vanadium_ppm"], view["vanadium_grade"])],
            t("pc_c_hgi"): view["hgi"].fillna("–").astype(str).str.replace(".0", "", regex=False),
            t("pc_c_price"): view["price"].map(lambda v: f"{v:,.1f}"),
            t("pc_c_date"): [fmt_month(d) if m else d.strftime("%Y-%m-%d")
                             for d, m in zip(view["date"], view["monthly"])],
            t("pc_c_age"): [f"{FRESH_ICON[l]} {age_text(d, l)}" for d, l in zip(view["age_days"], view["freshness"])],
            t("pc_c_change"): [("–" if pd.isna(c) else f"{c:+.1%}") + ("" if pd.isna(pd_) else
                               f" ({t('pc_vs')} {pd_.strftime('%Y-%m-%d')})")
                               for c, pd_ in zip(view["change_pct"], view["prev_date"])],
            t("pc_c_type"): [method_text(r) for _, r in view.iterrows()],
            t("pc_c_source"): view["source"].map(short_source),
            t("pc_c_link"): view["source_url"],
        })
        st.dataframe(table, hide_index=True, width="stretch",
                     column_config={t("pc_c_link"): st.column_config.LinkColumn(display_text=t("open"))})
        st.caption(t("pc_table_note"))

    # ------------------------------------------------------------ comparison chart
    st.subheader(t("pc_cmp_title"))
    cmp_group = g if g != "all" else st.radio(
        t("pc_cmp_pick"), ["cfr", "fob", "customs"], format_func=groups.get, horizontal=True, key="pc_cmp")
    cmp = lt[lt["basis"].map(basis_group) == cmp_group]
    if sg != "all":
        cmp = cmp[cmp["sulfur_grade"] == sg]
    if only_recent:
        cmp = cmp[cmp["freshness"] != "stale"]
    if cmp.empty:
        st.info(t("pc_empty_filter"))
    else:
        cmp = cmp.sort_values("price")
        labels = [f"{market(m) if cmp_group != 'customs' else country(o)} · "
                  f"{sulfur_text(s, gr)}" for m, o, s, gr in
                  zip(cmp["market"], cmp["origin"], cmp["sulfur_pct"], cmp["sulfur_grade"])]
        dates = [fmt_month(d) if m else d.strftime("%Y-%m-%d") for d, m in zip(cmp["date"], cmp["monthly"])]
        fig = go.Figure(go.Bar(
            x=cmp["price"], y=labels, orientation="h",
            marker_color=[FRESH_COLOR[l] for l in cmp["freshness"]],
            text=[f"{p:,.1f}  ({d})" for p, d in zip(cmp["price"], dates)], textposition="outside",
            hovertemplate="%{y}<br>%{x:.1f} " + t("pc_unit") + "<extra></extra>"))
        fig.update_layout(height=34 * len(cmp) + 90, margin=dict(l=10, r=120, t=10, b=30),
                          xaxis_title=t("pc_unit"))
        fig.update_xaxes(range=[0, float(cmp["price"].max()) * 1.35])
        st.plotly_chart(fig, width="stretch")
        st.caption(t("pc_cmp_note"))

    # ------------------------------------------------------------ forecasts
    st.subheader(t("pc_fc_title"))
    benchmark = None
    try:
        hist, fc, info = forecast_for("Green Pet Coke (GPC)")
        benchmark = benchmark_from(fc, hist, today + pd.Timedelta(days=30))
    except Exception:
        pass
    fc_rows, skipped = [], 0
    for _, r in lt.iterrows():
        if benchmark is None:
            break
        f = series_forecast(r, benchmark, today)
        if not f["ok"]:
            skipped += 1
            continue
        fc_rows.append({
            t("pc_c_origin"): country(r["origin"]), t("pc_c_market"): market(r["market"]),
            t("pc_c_basis"): basis_label(r["basis"]),
            t("pc_c_sulfur"): sulfur_text(r["sulfur_pct"], r["sulfur_grade"]),
            t("pc_f_last"): f"{fmt_price(r['price'])} ({r['date']:%Y-%m-%d})",
            t("pc_f_fc"): fmt_price(f["forecast"], r["price"]),
            t("pc_f_range"): f"{fmt_price(f['lo'], r['price'])} – {fmt_price(f['hi'], r['price'])}",
            t("pc_f_chg"): f"{f['change_pct']:+.1%}",
            t("pc_f_rel"): t("rel_low") if f["reliability"] == "low" else t("rel_mid"),
        })
    if benchmark is None:
        st.info(t("pc_fc_nobench"))
    elif fc_rows:
        st.markdown(t("pc_fc_intro", d=FORECAST_MAX_AGE))
        st.dataframe(pd.DataFrame(fc_rows), hide_index=True, width="stretch")
    else:
        st.info(t("pc_fc_none", d=FORECAST_MAX_AGE))
    if skipped:
        st.caption(t("pc_fc_skipped", n=skipped, d=FORECAST_MAX_AGE))

    # ------------------------------------------------------------ history
    multi = lt[lt["n_points"] >= 2]
    if not multi.empty:
        st.subheader(t("pc_hist_title"))
        names = {sid: f"{market(m)} · {basis_label(b)} · {sulfur_text(s, gr)}"
                 + (f" · {country(o)}" if "customs" in str(b).lower() else "")
                 for sid, m, b, s, gr, o in zip(multi["series_id"], multi["market"], multi["basis"],
                                                multi["sulfur_pct"], multi["sulfur_grade"], multi["origin"])}
        default = "COMTRADE_US_EXPORT" if "COMTRADE_US_EXPORT" in names else list(names)[0]
        picks = st.multiselect(t("pc_hist_pick"), list(names), default=[default],
                               format_func=names.get, key="pc_hist")
        hfig = go.Figure()
        for sid in picks:
            s = df[df["series_id"] == sid].sort_values("date")
            hfig.add_trace(go.Scatter(x=s["date"], y=s["price"], mode="lines+markers", name=names[sid]))
        hfig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=t("pc_unit"),
                           legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(hfig, width="stretch")

    # ------------------------------------------------------------ sources, limits, import
    with st.expander(t("pc_grades_title")):
        st.markdown(t("pc_grades_md", low=LOW_S, high=HIGH_S, v=LOW_V))
    with st.expander(t("pc_sources_title")):
        src = (df.groupby(["source", "source_url"], dropna=False)
               .agg(n=("price", "size"), last=("date", "max")).reset_index().sort_values("last", ascending=False))
        st.dataframe(pd.DataFrame({
            t("pc_c_source"): src["source"], t("pc_s_rows"): src["n"],
            t("pc_s_last"): src["last"].dt.strftime("%Y-%m-%d"), t("pc_c_link"): src["source_url"]}),
            hide_index=True, width="stretch",
            column_config={t("pc_c_link"): st.column_config.LinkColumn(display_text=t("open"))})
        st.markdown(t("pc_limits_md"))
    with st.expander(t("pc_import_title")):
        st.markdown(t("pc_import_md"))
        if TEMPLATE.exists():
            st.download_button(t("pc_template_btn"), TEMPLATE.read_bytes(),
                               file_name="gpc_prices_template.csv", mime="text/csv")
        buf = io.BytesIO()
        out = df.drop(columns=["as_of", "monthly"], errors="ignore").copy()
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        out.to_csv(buf, index=False, encoding="utf-8-sig")
        st.download_button(t("pc_all_btn"), buf.getvalue(), file_name="gpc_prices_all.csv", mime="text/csv")


def fmt_month(ts):
    return f"{ts.year}年{ts.month}月" if is_zh() else f"{ts:%b %Y}"
