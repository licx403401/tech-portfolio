#!/usr/bin/env python3
"""
update_from_api.py — 通过东方财富公开 API 获取基金和指数最新数据
替代硬编码 Wind 数据，实现每日自动更新。

数据源:
  - 基金净值: api.fund.eastmoney.com/f10/lsjz
  - 指数K线: push2his.eastmoney.com/api/qt/stock/kline/get

输出: data_202606.json（与现有格式完全兼容）
"""

import json
import math
import os
import random
from collections import Counter
import time
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta

# === 配置 ===
FUNDS = {
    "active": [
        ("012445", "华富新能源A"),
        ("019431", "永赢睿信A"),
        ("010350", "景顺品质长青A"),
        ("001323", "东吴移动互联A"),
        ("008657", "景顺科技创新A"),
        ("001437", "易方达瑞享I"),
        ("004815", "中欧红利优享C"),
        ("020219", "万家锦利C"),
        ("013840", "银华集成电路"),
        ("516120", "化工ETF"),
    ],
    "aggressive": [
        ("008282", "半导体ETF联接C"),
        ("008586", "人工智能ETF联接C"),
        ("001513", "信息产业A"),
        ("008657", "景顺科技创新A"),
        ("159781", "科创创业50ETF"),
        ("007817", "通信ETF联接A"),
        ("017653", "芯片产业A"),
        ("022184", "科技互联网C"),
        ("013840", "银华集成电路"),
    ],
}

# 历史持仓（今年以来的实际组合，复现 Sheet2"202604组合业绩回顾"）
# 这些是在今年大部分时间里真正持有的产品，用于计算 YTD 和图表净值序列
HISTORICAL_FUNDS = {
    "active": [
        ("012445", "华富新能源A"),
        ("019431", "永赢睿信A"),
        ("010350", "景顺品质长青A"),
        ("001323", "东吴移动互联A"),
        ("008657", "景顺科技创新A"),
        ("012520", "大成核心趋势"),    # 6月已调出，替换为易方达瑞享
        ("004815", "中欧红利优享C"),
        ("019518", "富国全球债券"),    # 6月已调出，替换为万家锦利
        ("516120", "化工ETF"),
    ],
    "aggressive": [
        ("008282", "半导体ETF联接C"),
        ("008586", "人工智能ETF联接C"),
        ("001513", "信息产业A"),
        ("001605", "国富沪港深成长精选"),  # 6月已调出
        ("008657", "景顺科技创新A"),
        ("159781", "科创创业50ETF"),
        ("007817", "通信ETF联接A"),
        ("017653", "芯片产业A"),
    ],
}

# 指数: section ID for Eastmoney
BENCHMARKS = {
    "000300": {"name": "沪深300", "secid": "1.000300"},
    "000906": {"name": "中证800", "secid": "1.000906"},
}

# 日期范围: 需要覆盖近1年 + 年初至今
# END_DATE 用于拉取数据的终止日（拉到今天，取最新可用点）
END_DATE = date.today().strftime("%Y-%m-%d")
ONE_YEAR_AGO = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
YEAR_START = f"{date.today().year}-01-01"
PREV_YEAR_END = f"{date.today().year - 1}-12-31"  # YTD base: last NAV of previous year

# 月度日期标签（从去年7月到当前月）
CURRENT_MONTH = date.today().month
CURRENT_YEAR = date.today().year
MONTHLY_DATES = []
for y in [CURRENT_YEAR - 1, CURRENT_YEAR]:
    for m in range(1, 13):
        if y == CURRENT_YEAR - 1 and m < 7:
            continue
        if y == CURRENT_YEAR and m > CURRENT_MONTH:
            break
        MONTHLY_DATES.append(f"{y}-{m:02d}-01")


def fetch_jsonp(url, params, referer, timeout=30):
    """Fetch JSONP endpoint and return parsed dict."""
    query = urllib.parse.urlencode(params)
    full_url = url + "?" + query
    req = urllib.request.Request(full_url, headers={
        "Referer": referer,
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    # Remove JSONP wrapper
    start = text.find("(") + 1
    end = text.rfind(")")
    if start <= 0 or end <= start:
        raise Exception(f"Invalid JSONP response: {text[:200]}")
    return json.loads(text[start:end])


def fetch_json(url, params, referer, timeout=30):
    """Fetch plain JSON API endpoint and return parsed dict."""
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"
    req = urllib.request.Request(full_url, headers={
        "Referer": referer,
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    return json.loads(text)


def get_fund_nav_history(code, start_date, end_date):
    """Fetch all NAV records for a fund."""
    url = "https://api.fund.eastmoney.com/f10/lsjz"
    referer = "https://fundf10.eastmoney.com/"

    # First page to get total count (Eastmoney caps at 20 records/page)
    PAGE_SIZE = 20
    params = {
        "callback": "jQuery",
        "fundCode": code,
        "pageIndex": 1,
        "pageSize": PAGE_SIZE,
        "startDate": start_date,
        "endDate": end_date,
        "_": int(time.time() * 1000),
    }

    data = fetch_jsonp(url, params, referer)
    if data.get("ErrCode") != 0:
        raise Exception(f"API error for fund {code}: {data.get('ErrMsg')}")

    nav_list = data["Data"]["LSJZList"]
    total = data.get("TotalCount", len(nav_list))

    # Fetch remaining pages
    total_pages = math.ceil(total / PAGE_SIZE)
    for page in range(2, total_pages + 1):
        params["pageIndex"] = page
        params["_"] = int(time.time() * 1000)
        time.sleep(0.2)  # Rate limiting
        data = fetch_jsonp(url, params, referer)
        nav_list.extend(data["Data"]["LSJZList"])

    # Sort by date ascending and deduplicate
    seen_dates = set()
    unique = []
    for n in sorted(nav_list, key=lambda x: x["FSRQ"]):
        if n["FSRQ"] not in seen_dates:
            seen_dates.add(n["FSRQ"])
            unique.append(n)
    return unique


def calc_returns(nav_list):
    """From ascending NAV list, compute YTD return, 1Y return, max DD."""
    if not nav_list:
        return {"ytd": None, "max_dd": None, "ret_1y": None}

    latest = nav_list[-1]
    latest_nav = float(latest["DWJZ"])
    latest_date = latest["FSRQ"]

    # --- YTD return (from last NAV on/before 12-31 of previous year) ---
    ytd_start = nav_list[0]
    for n in nav_list:
        if n["FSRQ"] > PREV_YEAR_END:
            break
        ytd_start = n  # keep updating to land on the last NAV <= 12-31
    ytd_nav = float(ytd_start["DWJZ"])
    ytd_return = round((latest_nav / ytd_nav - 1) * 100, 2)

    # --- 1-year return ---
    one_yr_start = nav_list[0]
    for n in nav_list:
        if n["FSRQ"] >= ONE_YEAR_AGO:
            one_yr_start = n
            break
    one_yr_nav = float(one_yr_start["DWJZ"])
    ret_1y = round((latest_nav / one_yr_nav - 1) * 100, 2)

    # --- Max drawdown this year (peak starts from year-beginning NAV) ---
    max_dd = 0.0
    # Peak = NAV at year start (Dec 31 of previous year)
    peak = ytd_nav
    for n in nav_list:
        if n["FSRQ"] >= YEAR_START:
            nav_val = float(n["DWJZ"])
            if nav_val > peak:
                peak = nav_val
            dd = (nav_val - peak) / peak if peak else 0
            if dd < max_dd:
                max_dd = dd

    max_dd_pct = round(max_dd * 100, 2)

    return {"ytd": ytd_return, "max_dd": max_dd_pct, "ret_1y": ret_1y}


def get_index_data(secid, cutoff_date=None):
    """Fetch daily index K-lines from PREV_YEAR_END to cutoff_date (default: today).

    cutoff_date: 'YYYY-MM-DD' string — use latest_data_date to align with fund NAV dates.

    Returns:
        monthly_returns: list of 12 monthly % returns (for chart NAV series)
        ytd_pct:         precise YTD % from 2025-12-31 close to cutoff close
        ret_1y_pct:      1-year % return
        max_dd_pct:      max drawdown since year start
    """
    end_str = (cutoff_date or END_DATE).replace("-", "")
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    referer = "https://quote.eastmoney.com/"
    # Fetch from ONE_YEAR_AGO (earlier than PREV_YEAR_END) to cover both YTD and 1Y base
    fetch_beg = ONE_YEAR_AGO.replace("-", "")
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",  # daily
        "fqt": "0",    # no forward adjustment
        "beg": fetch_beg,
        "end": end_str,
    }

    data = fetch_json(url, params, referer)
    if data.get("rc") != 0:
        raise Exception(f"Index API error: {data}")

    klines = data["data"]["klines"]
    if not klines:
        raise Exception("Empty klines response")

    # Parse all daily data
    daily = []  # [(date_str, close_float), ...]
    for line in klines:
        parts = line.split(",")
        daily.append((parts[0], float(parts[2])))

    latest_close = daily[-1][1]

    # --- Precise YTD: last close on/before PREV_YEAR_END (2025-12-31) as base ---
    ytd_base = None
    for dt, close in daily:
        if dt <= PREV_YEAR_END:
            ytd_base = close   # keep updating to land on the last day <= 12-31
        elif ytd_base is not None:
            break
    if ytd_base is None:
        ytd_base = daily[0][1]
    ytd_pct = round((latest_close / ytd_base - 1) * 100, 4)

    # --- 1-year return: first close on/after ONE_YEAR_AGO as base ---
    base_1y = daily[0][1]
    for dt, close in daily:
        if dt >= ONE_YEAR_AGO:
            base_1y = close
            break
    ret_1y_pct = round((latest_close / base_1y - 1) * 100, 4)

    # --- Max drawdown since YEAR_START ---
    peak = None
    max_dd = 0.0
    for dt, close in daily:
        if dt < YEAR_START:
            continue
        if peak is None or close > peak:
            peak = close
        dd = (close - peak) / peak if peak else 0
        if dd < max_dd:
            max_dd = dd
    max_dd_pct = round(max_dd * 100, 4)

    # --- Monthly returns for chart NAV series (from ONE_YEAR_AGO) ---
    monthly_close = {}
    for dt, close in daily:
        if dt < ONE_YEAR_AGO:
            continue
        month_key = dt[:7]
        monthly_close[month_key] = close  # keep last close of month

    sorted_months = sorted(monthly_close.keys())
    returns_pct = []
    prev_close = None
    for m in sorted_months:
        if prev_close is not None:
            ret = (monthly_close[m] / prev_close - 1) * 100
            returns_pct.append(round(ret, 4))
        prev_close = monthly_close[m]

    while len(returns_pct) < 12:
        returns_pct.insert(0, returns_pct[0] if returns_pct else None)
    if len(returns_pct) > 12:
        returns_pct = returns_pct[-12:]

    return returns_pct, ytd_pct, ret_1y_pct, max_dd_pct


def monthly_to_nav(returns_pct):
    """Convert monthly % returns to NAV series (starts at 1.0). None entries = flat."""
    nav = [1.0]
    for r in returns_pct:
        if r is None:
            nav.append(nav[-1])  # flat: no data available for this month
        else:
            nav.append(round(nav[-1] * (1.0 + r / 100.0), 6))
    return nav


def build_monthly_nav_series(yearly_return_pct, max_dd_pct, months=12, seed=42):
    """Generate realistic monthly NAV series matching given yearly return and max drawdown."""
    random.seed(seed)
    dd_depth = abs(max_dd_pct) / 100.0
    target_final = 1.0 + yearly_return_pct / 100.0

    nav = [1.0]
    # Phase 1: steady growth (~7 months)
    growth_months = min(7, months - 5)
    peak_needed = 1.0 / (1.0 - dd_depth) if dd_depth < 1.0 else 2.0
    monthly_growth = peak_needed ** (1.0 / growth_months) - 1.0 if growth_months > 0 else 0
    for _ in range(growth_months):
        noise = random.uniform(-0.008, 0.012)
        nav.append(nav[-1] * (1.0 + monthly_growth + noise))

    # Phase 2: drawdown (~3 months)
    dd_months = min(3, max(1, months - len(nav) - 2))
    for idx in range(dd_months):
        progress = (idx + 1) / (dd_months + 1)
        dip = -4 * progress * (1 - progress) * dd_depth
        monthly_r = dip / dd_months + random.uniform(-0.003, 0.003)
        nav.append(nav[-1] * (1.0 + monthly_r))

    # Phase 3: recovery to target
    for i in range(len(nav), months + 1):
        remaining = months - i + 1
        if remaining <= 0 or nav[-1] <= 0:
            break
        needed = target_final / nav[-1]
        if needed <= 0:
            break
        monthly_target = needed ** (1.0 / remaining) - 1.0
        noise = random.uniform(-0.005, 0.008)
        nav.append(nav[-1] * (1.0 + monthly_target + noise))

    # Force final NAV
    while len(nav) < months + 1:
        nav.append(nav[-1])
    nav[months] = target_final

    return [round(v, 4) for v in nav]


def calc_equal_weight(funds_list):
    """Calculate equal-weight average (YTD and 1Y only; max_dd handled separately).
    Skips funds with None (API-failed) values."""
    ytd_vals = [f["ytd"] for f in funds_list if f.get("ytd") is not None]
    ret1y_vals = [f["return_1y"] for f in funds_list if f.get("return_1y") is not None]
    return {
        "ytd": round(sum(ytd_vals) / len(ytd_vals), 2) if ytd_vals else None,
        "return_1y": round(sum(ret1y_vals) / len(ret1y_vals), 2) if ret1y_vals else None,
    }


def portfolio_daily_max_dd(fund_codes, fund_navs, year_start="2026-01-01"):
    """Equal-weight daily portfolio max drawdown since year_start.
    
    Returns (max_dd_pct, max_dd_date, peak_date) — portfolio-level, not single-fund worst.
    """
    all_dates = set()
    for code in fund_codes:
        for n in fund_navs.get(code, []):
            if n["FSRQ"] >= year_start:
                all_dates.add(n["FSRQ"])
    all_dates = sorted(all_dates)
    if not all_dates:
        return 0.0, None, None

    peak = None
    max_dd = 0.0
    max_dd_date = None
    peak_date = None

    for d in all_dates:
        nav_vals = []
        for code in fund_codes:
            for n in fund_navs.get(code, []):
                if n["FSRQ"] == d:
                    nav_vals.append(float(n["DWJZ"]))
                    break
        if not nav_vals:
            continue
        nav = sum(nav_vals) / len(nav_vals)
        if peak is None or nav > peak:
            peak = nav
            peak_date = d
        dd = (nav - peak) / peak if peak else 0
        if dd < max_dd:
            max_dd = dd
            max_dd_date = d

    return round(max_dd * 100, 2), max_dd_date, peak_date


def get_month_end_dates(fund_navs, latest_data_date):
    """Generate month-end dates from union of all fund NAV dates."""
    all_dates = sorted(set(n["FSRQ"] for nav_list in fund_navs.values() for n in nav_list if nav_list))
    month_end_dates = []
    for d in all_dates:
        if not month_end_dates or d[:7] != month_end_dates[-1][:7]:
            month_end_dates.append(d)
        else:
            month_end_dates[-1] = d
    # Ensure last point is the actual latest data date
    if month_end_dates:
        month_end_dates[-1] = latest_data_date
    return month_end_dates


def build_portfolio_equal_weight_nav(portfolio_funds, fund_navs, month_end_dates):
    """Build real equal-weight portfolio NAV series at each month-end date."""
    # Build date -> nav lookup for each fund in the portfolio
    code_nav = {}
    for f in portfolio_funds:
        code = f["code"].replace(".OF", "")
        if code not in fund_navs or not fund_navs[code]:
            continue
        nav_dict = {}
        for n in fund_navs[code]:
            nav_dict[n["FSRQ"]] = float(n["DWJZ"])
        code_nav[code] = nav_dict

    nav_series = []
    for target in month_end_dates:
        vals = []
        for nav_dict in code_nav.values():
            if target in nav_dict:
                vals.append(nav_dict[target])
            else:
                prev_dates = [d for d in nav_dict if d <= target]
                if prev_dates:
                    vals.append(nav_dict[max(prev_dates)])
                else:
                    vals.append(nav_dict[min(nav_dict)])
        if vals:
            nav_series.append(sum(vals) / len(vals))
        else:
            nav_series.append(nav_series[-1] if nav_series else 1.0)

    if not nav_series:
        return [1.0] * len(month_end_dates)
    base = nav_series[0]
    return [round(v / base, 6) for v in nav_series]


# ============================================================
# Main
# ============================================================

print("=" * 60)
print(f"  基金数据自动更新 — {END_DATE}")
print("=" * 60)

# === Step 1: Fetch fund data ===
print("\n[1/5] 获取基金净值数据...")
fund_returns = {}
fund_navs = {}  # Store full NAV series for each fund
fund_latest_dates = {}  # Track actual NAV dates per fund

all_funds = []
seen = set()
for ptype in ["active", "aggressive"]:
    for code, name in FUNDS[ptype]:
        if code not in seen:
            all_funds.append((ptype, code, name))
            seen.add(code)
# Also include historical funds that have been swapped out
for ptype in ["active", "aggressive"]:
    for code, name in HISTORICAL_FUNDS[ptype]:
        if code not in seen:
            all_funds.append((ptype, code, name))
            seen.add(code)

for idx, (ptype, code, name) in enumerate(all_funds):
    try:
        print(f"  [{idx+1}/{len(all_funds)}] {name} ({code})...", end=" ", flush=True)
        nav_list = get_fund_nav_history(code, ONE_YEAR_AGO, END_DATE)
        ret = calc_returns(nav_list)
        fund_returns[code] = ret
        fund_navs[code] = nav_list

        latest_date = nav_list[-1]["FSRQ"] if nav_list else "N/A"
        fund_latest_dates[code] = latest_date
        print(f"YTD={ret['ytd']:>7.2f}%  1Y={ret['ret_1y']:>7.2f}%  DD={ret['max_dd']:>7.2f}%  ({latest_date})")
    except Exception as e:
        print(f"FAILED: {e}")
        # Fallback: mark as missing instead of 0 (NaN/null signals data failure)
        fund_returns[code] = {"ytd": None, "max_dd": None, "ret_1y": None}
        fund_navs[code] = []
        fund_latest_dates[code] = None

# Use the most common actual NAV date (not today's date which may not have NAV yet)
valid_dates = [d for d in fund_latest_dates.values() if d and d != "N/A"]
date_counts = Counter(valid_dates)
latest_data_date = date_counts.most_common(1)[0][0] if date_counts else END_DATE
print(f"\n  📅 实际最新净值日: {latest_data_date}（各基金: {', '.join(f'{k}: {v}' for k, v in fund_latest_dates.items() if v)}）")

# === Step 2: Fetch benchmark monthly returns ===
print("\n[2/5] 获取基准指数数据...")
benchmark_monthly = {}
benchmark_precise = {}  # 精确的 ytd/1y/maxdd，来自日K而非月度推算
for code, info in BENCHMARKS.items():
    ok = False
    for attempt in range(3):  # 最多重试3次
        try:
            if attempt > 0:
                time.sleep(3)
                print(f"\n  [{info['name']}] 重试第{attempt}次...", end=" ", flush=True)
            else:
                print(f"  {info['name']} ({code})...", end=" ", flush=True)
            # 使用基金最新净值日作为截止，确保指数和基金数据时间点对齐
            monthly, ytd_pct, ret_1y_pct, max_dd_pct = get_index_data(info["secid"], cutoff_date=latest_data_date)
            benchmark_monthly[code] = monthly
            benchmark_precise[code] = {"ytd": ytd_pct, "return_1y": ret_1y_pct, "max_dd": max_dd_pct}
            final_close = monthly_to_nav(monthly)[-1]
            print(f"OK ({len(monthly)} months, YTD={ytd_pct:.2f}%  1Y={ret_1y_pct:.2f}%  DD={max_dd_pct:.2f}%)")
            ok = True
            break
        except Exception as e:
            print(f"FAILED: {e}")
    if not ok:
        # 保留上次成功的基准数据，不覆盖为0
        existing_bench = data.get("benchmarks", {}).get(code + ".SH", {})
        if existing_bench.get("ytd", 0) != 0:
            # 反推月度数据（近似）从现有ytd
            print(f"  ⚠ {info['name']} API 失败3次，保留上次数据（ytd={existing_bench.get('ytd')}%）")
            benchmark_monthly[code] = [None] * 12  # 图表用 None（前端显示间隙），但卡片指标从existing_bench保留
        else:
            benchmark_monthly[code] = [None] * 12

hs300_monthly = benchmark_monthly.get("000300", [None] * 12)
zz800_monthly = benchmark_monthly.get("000906", [None] * 12)

# === Step 3: Load existing JSON (if not exists, create default) ===
print("\n[3/5] 加载现有数据...")
json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_202606.json")
if os.path.exists(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
else:
    print("  (首次运行，创建默认结构)")
    data = {
        "data_date": "",
        "update_time": "",
        "config_text": "",
        "static_info": {"active_funds": [], "aggressive_funds": []},
        "portfolio": {
            "active": {"funds": [], "equal_weight": {}},
            "aggressive": {"funds": [], "equal_weight": {}}
        },
        "benchmarks": {},
        "monthly_series": {"dates": []},
        "rebalancing": {"active": [], "aggressive": []}
    }
    # Populate fund list from FUNDS config
    for ptype in ["active", "aggressive"]:
        for code, name in FUNDS[ptype]:
            data["portfolio"][ptype]["funds"].append({
                "code": code, "name": name,
                "ytd": 0, "return_1y": 0, "max_dd": 0,
                "latest_nav": 0, "latest_nav_date": ""
            })

# === Step 4: Update fund data ===
print("\n[4/5] 更新基金数据...")
for ptype in ["active", "aggressive"]:
    for fund in data["portfolio"][ptype]["funds"]:
        code_key = fund["code"].replace(".OF", "")
        if code_key in fund_returns:
            r = fund_returns[code_key]
            fund["ytd"] = r["ytd"]
            fund["max_dd"] = r["max_dd"]
            fund["return_1y"] = r["ret_1y"]
        else:
            print(f"  WARNING: {fund['name']} ({code_key}) 无数据，保持原有值")

# Recalculate equal_weight (YTD/1Y via average; MaxDD via portfolio-level daily series)
data["portfolio"]["active"]["equal_weight"] = calc_equal_weight(data["portfolio"]["active"]["funds"])
data["portfolio"]["aggressive"]["equal_weight"] = calc_equal_weight(data["portfolio"]["aggressive"]["funds"])

# Add portfolio-level max_dd to equal_weight
for ptype in ["active", "aggressive"]:
    ew_codes = [f["code"].replace(".OF", "") for f in data["portfolio"][ptype]["funds"]]
    ew_dd, ew_dd_date, ew_dd_peak = portfolio_daily_max_dd(ew_codes, fund_navs)
    data["portfolio"][ptype]["equal_weight"]["max_dd"] = ew_dd

for ptype in ["active", "aggressive"]:
    ew = data["portfolio"][ptype]["equal_weight"]
    print(f"  {ptype} 等权: YTD={ew['ytd']:.2f}%  1Y={ew['return_1y']:.2f}%  MaxDD={ew['max_dd']:.2f}%")

# Recalculate historical_weight — from HISTORICAL_FUNDS (Sheet2 actual holdings during the year)
for ptype in ["active", "aggressive"]:
    hist_codes = [code for code, name in HISTORICAL_FUNDS[ptype]]
    hist_fund_data = {code: fund_returns[code] for code in hist_codes if code in fund_returns}
    
    if hist_fund_data:
        ytd_vals = [r["ytd"] for r in hist_fund_data.values() if r["ytd"] is not None]
        ret1y_vals = [r["ret_1y"] for r in hist_fund_data.values() if r["ret_1y"] is not None]
        hw_ytd = round(sum(ytd_vals) / len(ytd_vals), 2) if ytd_vals else None
        hw_1y = round(sum(ret1y_vals) / len(ret1y_vals), 2) if ret1y_vals else None
        hw_dd, hw_dd_date, hw_dd_peak = portfolio_daily_max_dd(hist_codes, fund_navs)
        print(f"  历史组合最大回撤: {hw_dd}%  峰值日: {hw_dd_peak} → 谷底日: {hw_dd_date}")
    else:
        hw_ytd = hw_1y = hw_dd = 0.0
        hw_dd_date = hw_dd_peak = None

    # Preserve recommend_since from existing data (do NOT overwrite it)
    existing_hw = data["portfolio"][ptype].get("historical_weight", {})
    data["portfolio"][ptype]["historical_weight"] = {
        "ytd": hw_ytd, "return_1y": hw_1y, "max_dd": hw_dd,
        "recommend_since": existing_hw.get("recommend_since", 0),
    }
    print(f"  {ptype} 历史持仓(YTD用): YTD={hw_ytd:.2f}%  (Sheet2参考: {15.21 if ptype=='active' else 57.33}%)  recommend_since={existing_hw.get('recommend_since', 0)}")

# === Step 5: Rebuild monthly NAV series ===
print("\n[5/5] 重建月度净值序列...")

# Build month-end dates from fund NAV union
month_end_dates = get_month_end_dates(fund_navs, latest_data_date)
print(f"  月度节点: {month_end_dates}")

# Benchmarks from real data
hs300_nav = monthly_to_nav(hs300_monthly)
zz800_nav = monthly_to_nav(zz800_monthly)
print(f"  HS300: 12-month ret={((hs300_nav[-1]/hs300_nav[0])-1)*100:.2f}%")
print(f"  ZZ800: 12-month ret={((zz800_nav[-1]/zz800_nav[0])-1)*100:.2f}%")

# Portfolio NAV series — use HISTORICAL holdings for YTD accuracy
# Build lookup dicts from historical fund lists
historical_fund_holdings = {}
for ptype in ["active", "aggressive"]:
    historical_fund_holdings[ptype] = [
        {"code": code + ".OF", "name": name}
        for code, name in HISTORICAL_FUNDS[ptype]
    ]

active_nav = build_portfolio_equal_weight_nav(historical_fund_holdings["active"], fund_navs, month_end_dates)
agg_nav = build_portfolio_equal_weight_nav(historical_fund_holdings["aggressive"], fund_navs, month_end_dates)

# Ensure exactly 13 points
while len(month_end_dates) > 13:
    month_end_dates.pop(0)
    active_nav.pop(0)
    agg_nav.pop(0)
    hs300_nav.pop(0)
    zz800_nav.pop(0)
while len(month_end_dates) < 13:
    month_end_dates.append(month_end_dates[-1])
    active_nav.append(active_nav[-1])
    agg_nav.append(agg_nav[-1])
    hs300_nav.append(hs300_nav[-1])
    zz800_nav.append(zz800_nav[-1])

# Find the index for previous year-end (2025-12-31) in month_end_dates
# This is the reference point for YTD calculation in the chart
prev_year_end_idx = 0
for i, d in enumerate(month_end_dates):
    if d.startswith("2025-12"):
        prev_year_end_idx = i

# Store chart-derived metrics separately (used for chart display, NOT the authoritative YTD)
# Note: chart YTD may differ slightly from historical_weight because the chart uses
# month-end discrete points, while historical_weight is the simple average of fund YTDs.
# The card and risk table should use historical_weight, NOT chart_metrics.
for ptype, nav in [("active", active_nav), ("aggressive", agg_nav)]:
    ytd = (nav[-1] / nav[prev_year_end_idx] - 1) * 100
    ret_1y = (nav[-1] / nav[0] - 1) * 100
    peak = nav[0]
    max_dd = 0.0
    for v in nav:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < max_dd:
            max_dd = dd
    data["portfolio"][ptype]["chart_metrics"] = {
        "ytd": round(ytd, 2),
        "return_1y": round(ret_1y, 2),
        "max_dd": round(max_dd * 100, 2),
    }
    hw = data["portfolio"][ptype]["historical_weight"]
    print(f"  {ptype} 图表: YTD={ytd:.2f}% (卡片用历史持仓: {hw['ytd']:.2f}%)")

# Update benchmark metrics — use precise values from daily K-lines (NOT month-end nav interpolation)
for idx_code, name in [("000300", "沪深300"), ("000906", "中证800")]:
    json_code = idx_code + ".SH"
    precise = benchmark_precise.get(idx_code)
    monthly_list = benchmark_monthly.get(idx_code, [])
    if precise is None:
        # API failed, preserve last good data
        existing = data["benchmarks"].get(json_code, {})
        if existing.get("ytd", 0) != 0:
            print(f"  ⚠ {name} API失败，保留上次数据 ytd={existing['ytd']}%  1y={existing['return_1y']}%")
        continue
    data["benchmarks"][json_code] = {
        "name": name,
        "ytd": precise["ytd"],
        "return_1y": precise["return_1y"],
        "max_dd": precise["max_dd"],
    }
    print(f"  {name}: YTD={precise['ytd']:.2f}%  1Y={precise['return_1y']:.2f}%  MaxDD={precise['max_dd']:.2f}%")

data["monthly_series"] = {
    "dates": month_end_dates,
    "active_nav": active_nav,
    "aggressive_nav": agg_nav,
    "hs300_nav": hs300_nav,
    "zz800_nav": zz800_nav,
}
data["data_date"] = latest_data_date
data["update_time"] = latest_data_date

print(f"  图表日期: {month_end_dates[0]} → {month_end_dates[-1]}")

# === Save ===
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n{'=' * 60}")
print(f"  ✅ 完成！输出文件: {json_path}")
print(f"  数据日期: {latest_data_date}")
print(f"{'=' * 60}")
