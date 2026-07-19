"""
Single-stock data fetcher for the local stock decision engine.

Usage:
  python stock_decision_data.py 601138 --period short

Output:
  data/stock_json/stock_data_601138.json

The script fetches objective data only and runs mechanical checks that do not
require investment judgment. The final buy/watch/avoid decision should still be
made against 股票投资决策引擎（AI执行版 v2.0）.md.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

import time as _time
import warnings

import requests


EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://quote.eastmoney.com/",
}

# ---------------------------------------------------------------------------
# 共享 HTTP 会话。
#
# 默认忽略环境变量里的 HTTP_PROXY / HTTPS_PROXY，避免本机代理不稳定时导致
# 东方财富接口偶发失败。确实需要代理时，通过 CLI 的 --proxy 参数显式传入。
# ---------------------------------------------------------------------------
_SESSION = requests.Session()
_SESSION.trust_env = False
_EastmoneyProxy: str | None = None  # 由 CLI --proxy 设置


def _build_session() -> requests.Session:
    """返回请求会话；如设置了 --proxy，则优先使用显式代理。"""
    if _EastmoneyProxy:
        session = requests.Session()
        session.proxies = {"http": _EastmoneyProxy, "https": _EastmoneyProxy}
        session.trust_env = False
        return session
    return _SESSION


def exchange_prefix(code: str) -> str:
    # 东方财富/腾讯接口用 1 表示沪市，0 表示深市。
    if code.startswith(("6", "9")):
        return "1"
    if code.startswith(("0", "2", "3")):
        return "0"
    raise ValueError(f"Unsupported A-share code: {code}")


def market_suffix(code: str) -> str:
    return "SH" if exchange_prefix(code) == "1" else "SZ"


def safe_float(value: Any, scale: float = 1.0) -> float | None:
    # 行情/财务接口经常用 "-", "", None 表示缺失值，统一转成 None。
    if value in (None, "-", ""):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    return value / scale


def round_or_none(value: float | None, ndigits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, ndigits)


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def percentile_rank(values: list[float], current: float | None) -> float | None:
    # 返回 current 在历史样本中的百分位：数值越高，代表估值/指标越靠近历史高位。
    values = [value for value in values if value is not None and value > 0]
    if not values or current is None:
        return None
    below_or_equal = sum(1 for value in values if value <= current)
    return below_or_equal / len(values) * 100


def request_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    retries: int = 3,
    backoff: float = 1.5,
) -> dict[str, Any]:
    """GET 请求并解析 JSON；对临时网络错误做重试和指数退避。"""
    session = _build_session()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, headers=EASTMONEY_HEADERS, timeout=25)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            # 4xx 通常是参数或权限问题，重试没有意义，直接抛出。
            if 400 <= (exc.response.status_code if exc.response is not None else 400) < 500:
                raise
            last_err = exc
        except requests.exceptions.RequestException as exc:
            last_err = exc
        if attempt < retries - 1:
            _time.sleep(backoff**attempt)
    raise RuntimeError(f"request_json failed after {retries} attempts: {url}") from last_err


def request_json_safe(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    retries: int = 2,
    label: str = "",
) -> dict[str, Any] | None:
    """request_json 的宽松版本；失败时返回 None。

    用于公告、股东户数等非核心接口，避免单个数据缺口中断整次抓取。
    """
    try:
        return request_json(url, params=params, retries=retries)
    except Exception as exc:
        warnings.warn(f"[data-gap] {label or url} unreachable: {exc}", stacklevel=2)
        return None


# ---------------------------------------------------------------------------
# 腾讯实时行情（qt.gtimg.cn）。
#
# 部分网络环境会屏蔽东方财富 push2 接口；腾讯行情接口更容易直连，并且能覆盖
# 决策引擎当前所需的价格、成交额、PE、PB、市值等字段。
# ---------------------------------------------------------------------------

# 腾讯行情字段下标，原始字符串按 "~" 分隔。
_TQ_PRICE = 3
_TQ_PREV_CLOSE = 4
_TQ_OPEN = 5
_TQ_VOLUME_LOTS = 6
_TQ_HIGH = 33
_TQ_LOW = 34
_TQ_AMOUNT_WAN = 37  # 万元
_TQ_PE_TTM = 39
_TQ_MARKET_CAP_YI = 44  # 亿元
_TQ_PB = 46


def _tencent_quote_url(code: str) -> str:
    prefix = "sh" if exchange_prefix(code) == "1" else "sz"
    return f"https://qt.gtimg.cn/q={prefix}{code}"


def fetch_quote(code: str) -> dict[str, Any]:
    """从腾讯 qt.gtimg.cn 获取实时行情快照。"""
    url = _tencent_quote_url(code)
    session = _build_session()
    resp = session.get(url, headers=EASTMONEY_HEADERS, timeout=15)
    resp.raise_for_status()
    # 返回格式示例：v_sh601138="field0~field1~..."
    text = resp.text
    # 只保留双引号内部的字段串。
    if '"' in text:
        text = text.split('"')[1]
    parts = text.split("~")
    name = parts[1] if len(parts) > 1 else None

    def _f(idx: int, scale: float = 1.0) -> float | None:
        if idx < len(parts) and parts[idx] not in ("", "-"):
            return safe_float(parts[idx], scale)
        return None

    price = _f(_TQ_PRICE)
    market_cap_yi = _f(_TQ_MARKET_CAP_YI)

    return {
        "code": code,
        "name": name,
        "market": market_suffix(code),
        # The endpoint fields parsed here do not expose a reliable trade date.
        # build_payload() assigns the latest confirmed daily K-line date.
        "trade_date": None,
        "price": round_or_none(price),
        "open": round_or_none(_f(_TQ_OPEN)),
        "high": round_or_none(_f(_TQ_HIGH)),
        "low": round_or_none(_f(_TQ_LOW)),
        "previous_close": round_or_none(_f(_TQ_PREV_CLOSE)),
        "change": round_or_none(None if price is None or _f(_TQ_PREV_CLOSE) is None else price - _f(_TQ_PREV_CLOSE)),  # type: ignore[arg-type]
        "pct_change": round_or_none(
            None
            if price is None or _f(_TQ_PREV_CLOSE) is None
            else (price - _f(_TQ_PREV_CLOSE)) / _f(_TQ_PREV_CLOSE) * 100  # type: ignore[operator,arg-type]
        ),
        "volume_lots": _f(_TQ_VOLUME_LOTS),
        "amount_yuan": (
            None if (a := _f(_TQ_AMOUNT_WAN)) is None else a * 10000
        ),
        "market_cap_yuan": None if market_cap_yi is None else market_cap_yi * 1e8,
        "pe_ttm": round_or_none(_f(_TQ_PE_TTM)),
        "pb": round_or_none(_f(_TQ_PB)),
        "dividend_yield_pct": None,  # 腾讯接口不提供，后续可由财报/分红数据补充。
        "roe_pct_quote": None,  # 腾讯接口不提供。
        "net_margin_pct_quote": None,
        "trading_status": None,
    }


# ---------------------------------------------------------------------------
# 腾讯日 K 线（web.ifzq.gtimg.cn）。
#
# 每行格式：[date, open, close, high, low, volume(lots)]。
# ---------------------------------------------------------------------------

_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def parse_kline(item: list[Any], prev_close: float | None = None) -> dict[str, Any]:
    """解析腾讯格式的单行 K 线。"""
    dt = str(item[0])
    o = float(item[1])
    c = float(item[2])
    h = float(item[3])
    lo = float(item[4])
    vol = float(item[5]) if len(item) > 5 else 0.0
    # 腾讯 K 线不直接给昨收，调用方把上一根 K 线的收盘价传进来。
    pc = prev_close if prev_close is not None else o
    pct_change = (c / pc - 1) * 100 if pc and pc != 0 else 0.0
    amplitude = (h - lo) / pc * 100 if pc and pc != 0 else 0.0
    return {
        "date": dt if len(dt) <= 10 else dt[:10],
        "open": o,
        "close": c,
        "high": h,
        "low": lo,
        "volume": vol,
        "amount": 0.0,  # 腾讯 K 线不包含成交额。
        "amplitude_pct": round(amplitude, 2),
        "pct_change": round(pct_change, 2),
        "change": round(c - pc, 2),
        "turnover_pct": 0.0,  # 腾讯 K 线不包含换手率。
    }


def fetch_daily_klines(
    code: str, begin: str = "20180101", limit: int = 1200
) -> list[dict[str, Any]]:
    """获取腾讯前复权日 K；失败时回退到东方财富历史 K 线接口。"""
    prefix = "sh" if exchange_prefix(code) == "1" else "sz"
    raw = request_json_safe(
        _TENCENT_KLINE_URL,
        params={
            "param": f"{prefix}{code},day,,,{limit},qfq",
        },
        retries=2,
        label="tencent-klines",
    )
    if raw is None:
        # 回退方案：尝试东方财富 push2his，某些网络环境下仍可用。
        return _fetch_klines_eastmoney(code, begin, limit)
    try:
        raw_rows = raw.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday") or []
    except Exception:
        raw_rows = []
    result: list[dict[str, Any]] = []
    prev_close: float | None = None
    for row in raw_rows:
        parsed = parse_kline(row, prev_close)
        result.append(parsed)
        prev_close = parsed["close"]
    return result


def _fetch_klines_eastmoney(code: str, begin: str, limit: int) -> list[dict[str, Any]]:
    """旧版东方财富 K 线抓取器，作为腾讯接口失败时的兜底。"""
    # 东方财富返回逗号分隔字符串，这里沿用旧解析方式并包装为 dict。
    def _legacy_parse(line: str) -> dict[str, Any]:
        parts = line.split(",")
        return {
            "date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "amount": float(parts[6]),
            "amplitude_pct": float(parts[7]),
            "pct_change": float(parts[8]),
            "change": float(parts[9]),
            "turnover_pct": float(parts[10]),
        }

    secid = f"{exchange_prefix(code)}.{code}"
    raw = request_json_safe(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": begin,
            "end": "20500101",
            "lmt": str(limit),
        },
        retries=2,
        label="eastmoney-klines-fallback",
    )
    if raw is None:
        return []
    return [
        _legacy_parse(line) for line in (raw.get("data") or {}).get("klines") or []
    ]


def moving_average(rows: list[dict[str, Any]], window: int) -> float | None:
    # 简单移动平均线，样本不足时返回 None，避免产生误导性指标。
    if len(rows) < window:
        return None
    return statistics.fmean(row["close"] for row in rows[-window:])


def pct_change_from(rows: list[dict[str, Any]], periods: int) -> float | None:
    # 计算相对 N 个交易日前收盘价的涨跌幅。
    if len(rows) <= periods:
        return None
    previous = rows[-periods - 1]["close"]
    if previous == 0:
        return None
    return (rows[-1]["close"] / previous - 1) * 100


def calc_atr_pct(rows: list[dict[str, Any]], window: int = 14) -> float | None:
    # ATR 用于衡量波动率；这里转成占最新收盘价的百分比，便于不同股价之间比较。
    if len(rows) <= window:
        return None
    trs: list[float] = []
    for idx in range(len(rows) - window, len(rows)):
        row = rows[idx]
        prev_close = rows[idx - 1]["close"]
        true_range = max(
            row["high"] - row["low"],
            abs(row["high"] - prev_close),
            abs(row["low"] - prev_close),
        )
        trs.append(true_range)
    close = rows[-1]["close"]
    if close == 0:
        return None
    return statistics.fmean(trs) / close * 100


def build_technical_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # 汇总决策引擎需要的趋势、涨跌幅、波动率和最近 K 线。
    if not rows:
        return {"error": "no kline data"}
    last = rows[-1]
    ma20 = moving_average(rows, 20)
    ma60 = moving_average(rows, 60)
    ma120 = moving_average(rows, 120)
    previous_ma20 = moving_average(rows[:-1], 20)
    previous_ma60 = moving_average(rows[:-1], 60)
    close = last["close"]
    high20 = max(row["high"] for row in rows[-20:]) if len(rows) >= 20 else None
    low20 = min(row["low"] for row in rows[-20:]) if len(rows) >= 20 else None
    return {
        "last_date": last["date"],
        "close": round(close, 2),
        "day_pct_change": round(last["pct_change"], 2),
        "amount_yuan": last["amount"],
        "ma20": round_or_none(ma20),
        "ma60": round_or_none(ma60),
        "ma120": round_or_none(ma120),
        "ma20_slope_up": None if ma20 is None or previous_ma20 is None else ma20 > previous_ma20,
        "ma60_slope_up": None if ma60 is None or previous_ma60 is None else ma60 > previous_ma60,
        "above_ma20": None if ma20 is None else close >= ma20,
        "above_ma60": None if ma60 is None else close >= ma60,
        "change_5d_pct": round_or_none(pct_change_from(rows, 5)),
        "change_20d_pct": round_or_none(pct_change_from(rows, 20)),
        "change_60d_pct": round_or_none(pct_change_from(rows, 60)),
        "high_20d": round_or_none(high20),
        "low_20d": round_or_none(low20),
        "atr14_pct": round_or_none(calc_atr_pct(rows, 14)),
        "recent_10d": rows[-10:],
    }


def fetch_financial_main(code: str, page_size: int = 40) -> list[dict[str, Any]]:
    # 东方财富 F10 主要财务指标，按报告期倒序返回。
    secucode = f"{code}.{market_suffix(code)}"
    raw = request_json(
        "https://datacenter.eastmoney.com/securities/api/data/v1/get",
        params={
            "reportName": "RPT_F10_FINANCE_MAINFINADATA",
            "columns": "ALL",
            "filter": f'(SECUCODE="{secucode}")',
            "pageNumber": "1",
            "pageSize": str(page_size),
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
        },
    )
    return (raw.get("result") or {}).get("data") or []


def simplify_financial_row(row: dict[str, Any]) -> dict[str, Any]:
    # 将东方财富原始字段名转换为更稳定、可读的内部字段名。
    parent_np = safe_float(row.get("PARENTNETPROFIT"))
    ocf = safe_float(row.get("NETCASH_OPERATE_PK"))
    return {
        "report": row.get("REPORT_DATE_NAME"),
        "report_type": row.get("REPORT_TYPE"),
        "notice_date": (row.get("NOTICE_DATE") or "")[:10] or None,
        "revenue_yuan": safe_float(row.get("TOTALOPERATEREVE")),
        "parent_net_profit_yuan": parent_np,
        "deducted_net_profit_yuan": safe_float(row.get("KCFJCXSYJLR")),
        "operating_cashflow_yuan": ocf,
        "ocf_to_net_profit": None if not parent_np else ocf / parent_np if ocf is not None else None,
        "eps": safe_float(row.get("EPSJB")),
        "deducted_eps": safe_float(row.get("EPSKCJB")),
        "roe_pct": safe_float(row.get("ROEJQ")),
        "gross_margin_pct": safe_float(row.get("XSMLL")),
        "net_margin_pct": safe_float(row.get("XSJLL")),
        "debt_asset_ratio_pct": safe_float(row.get("ZCFZL")),
        "free_cashflow_yuan": safe_float(row.get("FCFF_FORWARD")),
        "revenue_yoy_pct": safe_float(row.get("TOTALOPERATEREVETZ")),
        "parent_np_yoy_pct": safe_float(row.get("PARENTNETPROFITTZ")),
        "deducted_np_yoy_pct": safe_float(row.get("KCFJCXSYJLRTZ")),
        "total_share": safe_float(row.get("TOTAL_SHARE")),
    }


def financial_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # 财务快照只做机械筛查，不直接给投资结论。
    simplified = [simplify_financial_row(row) for row in rows]
    annual = [row for row in simplified if row["report_type"] == "年报"]
    latest = simplified[0] if simplified else None
    last_three_annual = annual[:3]

    ocf_ratios = [row["ocf_to_net_profit"] for row in last_three_annual]
    # 连续三年经营现金流/归母净利润低于 0.5，标记为现金流质量风险。
    consecutive_low_ocf = (
        len(ocf_ratios) == 3
        and all(ratio is not None and ratio < 0.5 for ratio in ocf_ratios)
    )
    # 最近两年扣非净利润为负，标记为盈利质量风险。
    deducted_negative_two_years = (
        len(last_three_annual) >= 2
        and all((row["deducted_net_profit_yuan"] or 0) < 0 for row in last_three_annual[:2])
    )

    return {
        "latest": latest,
        "annual_last_3": last_three_annual,
        "recent_reports": simplified[:8],
        "checks": {
            "deducted_net_profit_negative_latest_2y": deducted_negative_two_years,
            "ocf_to_net_profit_below_0_5_latest_3y": consecutive_low_ocf,
            "latest_annual_ocf_to_net_profit": round_or_none(
                annual[0]["ocf_to_net_profit"] if annual else None
            ),
        },
    }


def _fiscal_year(row: dict[str, Any]) -> int | None:
    report_date = parse_date(row.get("REPORT_DATE"))
    return report_date.year if report_date else None


def _period_key(row: dict[str, Any]) -> str | None:
    # 将中文报告类型归一成估值计算使用的期间键。
    report_type = row.get("REPORT_TYPE")
    if report_type == "一季报":
        return "q1"
    if report_type == "中报":
        return "h1"
    if report_type == "三季报":
        return "q3"
    if report_type == "年报":
        return "fy"
    return None


def _ttm_eps_for_row(row: dict[str, Any], lookup: dict[tuple[int, str], dict[str, Any]]) -> float | None:
    # TTM EPS = 当期累计 EPS + 上年年报 EPS - 上年同期累计 EPS。
    year = _fiscal_year(row)
    period = _period_key(row)
    eps = safe_float(row.get("EPSJB"))
    if year is None or period is None or eps is None:
        return None
    if period == "fy":
        return eps
    previous_annual = safe_float((lookup.get((year - 1, "fy")) or {}).get("EPSJB"))
    previous_same_period = safe_float((lookup.get((year - 1, period)) or {}).get("EPSJB"))
    if previous_annual is None or previous_same_period is None:
        return None
    return eps + previous_annual - previous_same_period


def _book_value_per_share(row: dict[str, Any]) -> float | None:
    # 用 ROE 反推归母权益，再除以总股本，得到粗略每股净资产。
    parent_np = safe_float(row.get("PARENTNETPROFIT"))
    roe_pct = safe_float(row.get("ROEJQ"))
    total_share = safe_float(row.get("TOTAL_SHARE"))
    if parent_np is None or roe_pct is None or roe_pct <= 0 or not total_share:
        return None
    equity = parent_np / (roe_pct / 100)
    return equity / total_share


def valuation_snapshot(
    quote: dict[str, Any],
    klines: list[dict[str, Any]],
    financial_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """用日线价格和已披露财报粗略估算历史 PE/PB 区间。

    这样可以减少额外行情依赖。结果适合初筛，JSON 中会明确标记为 derived，
    估值判断仍需人工复核口径。
    """
    lookup: dict[tuple[int, str], dict[str, Any]] = {}
    reports: list[dict[str, Any]] = []
    for row in financial_rows:
        year = _fiscal_year(row)
        period = _period_key(row)
        notice_date = parse_date(row.get("NOTICE_DATE"))
        if year is None or period is None or notice_date is None:
            continue
        lookup[(year, period)] = row
        reports.append(row)

    effective_reports: list[dict[str, Any]] = []
    for row in reports:
        notice_date = parse_date(row.get("NOTICE_DATE"))
        ttm_eps = _ttm_eps_for_row(row, lookup)
        bvps = _book_value_per_share(row)
        if notice_date is None:
            continue
        effective_reports.append(
            {
                "notice_date": notice_date,
                "report": row.get("REPORT_DATE_NAME"),
                "ttm_eps": ttm_eps,
                "book_value_per_share": bvps,
            }
        )
    effective_reports.sort(key=lambda item: item["notice_date"])

    # 只使用近 5 年样本，且每个交易日只使用当时已经公告的最新财报。
    cutoff = date.today() - timedelta(days=365 * 5)
    pe_values: list[float] = []
    pb_values: list[float] = []
    report_idx = -1
    current_report: dict[str, Any] | None = None
    for kline in klines:
        kline_date = parse_date(kline.get("date"))
        if kline_date is None or kline_date < cutoff:
            continue
        # 将财报滚动推进到当前 K 线日期前已经披露的最新一份。
        while report_idx + 1 < len(effective_reports) and effective_reports[report_idx + 1]["notice_date"] <= kline_date:
            report_idx += 1
            current_report = effective_reports[report_idx]
        if current_report is None:
            continue
        close = safe_float(kline.get("close"))
        if close is None:
            continue
        ttm_eps = current_report.get("ttm_eps")
        if ttm_eps and ttm_eps > 0:
            pe_values.append(close / ttm_eps)
        bvps = current_report.get("book_value_per_share")
        if bvps and bvps > 0:
            pb_values.append(close / bvps)

    latest_row = effective_reports[-1] if effective_reports else {}
    current_pe = safe_float(quote.get("pe_ttm"))
    current_pb = safe_float(quote.get("pb"))
    return {
        "method": "derived_from_qfq_daily_close_and_reported_eps_roe",
        "pe_ttm": current_pe,
        "pb": current_pb,
        "pe_5y_median": round_or_none(statistics.median(pe_values) if pe_values else None),
        "pe_5y_percentile": round_or_none(percentile_rank(pe_values, current_pe)),
        "pb_5y_median": round_or_none(statistics.median(pb_values) if pb_values else None),
        "pb_5y_percentile": round_or_none(percentile_rank(pb_values, current_pb)),
        "sample_days_pe": len(pe_values),
        "sample_days_pb": len(pb_values),
        "latest_effective_report": {
            "report": latest_row.get("report"),
            "notice_date": latest_row.get("notice_date").isoformat() if latest_row.get("notice_date") else None,
            "ttm_eps": round_or_none(latest_row.get("ttm_eps")),
            "book_value_per_share": round_or_none(latest_row.get("book_value_per_share")),
        },
        "note": "历史PE/PB为脚本估算值，使用前复权日收盘价与财报披露后的EPS/ROE口径；重大分红送转场景需人工复核。",
    }


def fetch_shareholder_count(code: str, page_size: int = 8) -> dict[str, Any]:
    # 股东户数用于观察筹码集中度变化，接口通常按截止日期倒序返回。
    raw = request_json_safe(
        "https://datacenter.eastmoney.com/securities/api/data/v1/get",
        params={
            "reportName": "RPT_HOLDERNUM_DET",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": "1",
            "pageSize": str(page_size),
            "sortTypes": "-1",
            "sortColumns": "END_DATE",
        },
        retries=2,
        label="shareholder-count",
    )
    rows = ((raw.get("result") or {}).get("data") if raw else []) or []
    latest = rows[0] if rows else {}
    return {
        "latest": {
            "end_date": (latest.get("END_DATE") or "")[:10] or None,
            "holder_num": latest.get("HOLDER_NUM"),
            "previous_holder_num": latest.get("PRE_HOLDER_NUM"),
            "holder_num_change": latest.get("HOLDER_NUM_CHANGE"),
            "holder_num_change_pct": round_or_none(safe_float(latest.get("HOLDER_NUM_RATIO"))),
            "avg_hold_num": round_or_none(safe_float(latest.get("AVG_HOLD_NUM"))),
            "avg_market_cap_yuan": round_or_none(safe_float(latest.get("AVG_MARKET_CAP"))),
        },
        "recent": [
            {
                "end_date": (row.get("END_DATE") or "")[:10] or None,
                "holder_num": row.get("HOLDER_NUM"),
                "holder_num_change_pct": round_or_none(safe_float(row.get("HOLDER_NUM_RATIO"))),
            }
            for row in rows
        ],
    }


def fetch_margin_trading(code: str, page_size: int = 20) -> dict[str, Any]:
    # 融资融券余额用于观察杠杆资金变化；非两融标的可能返回空。
    raw = request_json_safe(
        "https://datacenter.eastmoney.com/securities/api/data/v1/get",
        params={
            "reportName": "RPTA_WEB_RZRQ_GGMX",
            "columns": "ALL",
            "filter": f'(SCODE="{code}")',
            "pageNumber": "1",
            "pageSize": str(page_size),
            "sortTypes": "-1",
            "sortColumns": "DATE",
        },
        retries=2,
        label="margin-trading",
    )
    rows = ((raw.get("result") or {}).get("data") if raw else []) or []
    latest = rows[0] if rows else {}
    return {
        "latest": {
            "date": (latest.get("DATE") or "")[:10] or None,
            "financing_balance_yuan": safe_float(latest.get("RZYE")),
            "securities_lending_balance_yuan": safe_float(latest.get("RQYE")),
            "margin_balance_yuan": safe_float(latest.get("RZRQYE")),
            "financing_buy_yuan": safe_float(latest.get("RZMRE")),
            "financing_repay_yuan": safe_float(latest.get("RZCHE")),
            "financing_net_buy_yuan": safe_float(latest.get("RZJME")),
            "financing_balance_to_market_cap_pct": round_or_none(safe_float(latest.get("RZYEZB"))),
            "financing_net_buy_5d_yuan": safe_float(latest.get("RZJME5D")),
            "financing_net_buy_10d_yuan": safe_float(latest.get("RZJME10D")),
        },
        "recent": [
            {
                "date": (row.get("DATE") or "")[:10] or None,
                "financing_balance_yuan": safe_float(row.get("RZYE")),
                "financing_net_buy_yuan": safe_float(row.get("RZJME")),
                "financing_balance_to_market_cap_pct": round_or_none(safe_float(row.get("RZYEZB"))),
            }
            for row in rows[:10]
        ],
    }


def fetch_northbound_holding(code: str) -> dict[str, Any]:
    # 北向持股是阶段性快照，不等同于当日净流入。
    raw = request_json_safe(
        "https://datacenter.eastmoney.com/securities/api/data/v1/get",
        params={
            "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": "1",
            "pageSize": "1",
            "sortTypes": "-1",
            "sortColumns": "TRADE_DATE",
        },
        retries=2,
        label="northbound-holding",
    )
    rows = ((raw.get("result") or {}).get("data") if raw else []) or []
    latest = rows[0] if rows else {}
    return {
        "latest": {
            "trade_date": (latest.get("TRADE_DATE") or "")[:10] or None,
            "hold_shares": safe_float(latest.get("HOLD_SHARES")),
            "hold_market_cap_yuan": safe_float(latest.get("HOLD_MARKET_CAP")),
            "a_shares_ratio_pct": round_or_none(safe_float(latest.get("A_SHARES_RATIO"))),
            "free_shares_ratio_pct": round_or_none(safe_float(latest.get("FREE_SHARES_RATIO"))),
            "close_price": safe_float(latest.get("CLOSE_PRICE")),
        },
        "note": "该接口可能为阶段性持股快照，日期滞后时不得当作当日北向流向。",
    }


def fetch_unlock_schedule(code: str, page_size: int = 20) -> dict[str, Any]:
    # 只抓取今天之后的限售解禁，用于识别未来供给压力。
    today = date.today().isoformat()
    raw = request_json_safe(
        "https://datacenter.eastmoney.com/securities/api/data/v1/get",
        params={
            "reportName": "RPT_LIFT_STAGE",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")(FREE_DATE>="{today}")',
            "pageNumber": "1",
            "pageSize": str(page_size),
            "sortTypes": "1",
            "sortColumns": "FREE_DATE",
        },
        retries=2,
        label="unlock-schedule",
    )
    rows = ((raw.get("result") or {}).get("data") if raw else []) or []
    return {
        "future_count": len(rows),
        "next": [
            {
                "free_date": (row.get("FREE_DATE") or "")[:10] or None,
                "free_shares_type": row.get("FREE_SHARES_TYPE"),
                "able_free_shares_10k": safe_float(row.get("ABLE_FREE_SHARES")),
                "current_free_shares_10k": safe_float(row.get("CURRENT_FREE_SHARES")),
                "total_ratio_pct": round_or_none(safe_float(row.get("TOTAL_RATIO"))),
                "lift_market_cap_10k_yuan": safe_float(row.get("LIFT_MARKET_CAP")),
            }
            for row in rows
        ],
    }


def fetch_announcements(code: str, keyword: str, page_size: int = 20) -> list[dict[str, Any]]:
    # 按关键词抓公告标题和 PDF 链接；公告实质内容仍需人工读原文。
    secucode = f"{code}.{market_suffix(code)}"
    raw = request_json_safe(
        "https://np-anotice-stock.eastmoney.com/api/security/ann",
        params={
            "sr": "-1",
            "page_size": str(page_size),
            "page_index": "1",
            "ann_type": "A",
            "client_source": "web",
            "stock_list": secucode,
            "f_node": "0",
            "s_node": "0",
            "keyword": keyword,
        },
        retries=2,
        label=f"announcements keyword={keyword}",
    )
    items = (raw.get("data") or {}).get("list") if raw else []
    result = []
    for item in items:
        result.append(
            {
                "title": item.get("title"),
                "notice_date": item.get("notice_date"),
                "columns": item.get("columns"),
                "art_code": item.get("art_code"),
                "pdf_url": (
                    f"https://pdf.dfcfw.com/pdf/H2_{item.get('art_code')}_1.pdf"
                    if item.get("art_code")
                    else None
                ),
            }
        )
    return result


def build_data_gaps(payload: dict[str, Any]) -> list[str]:
    # 汇总无法可靠自动化的数据缺口，提醒决策引擎后续人工补齐。
    gaps: list[str] = []
    valuation = payload.get("valuation") or {}
    if valuation.get("pe_5y_median") is None or valuation.get("sample_days_pe", 0) < 200:
        gaps.append("近5年PE中位数/分位数样本不足，需另行补充或人工确认")
    if not payload.get("earnings_forecast", {}).get("future_eps"):
        gaps.append("未来第3年EPS预估及假设需另行补充")
    ownership = payload.get("ownership") or {}
    if not (ownership.get("shareholder_count") or {}).get("latest", {}).get("holder_num"):
        gaps.append("股东户数未抓取成功")
    if not (ownership.get("margin_trading") or {}).get("latest", {}).get("date"):
        gaps.append("融资余额未抓取成功")
    if not (ownership.get("northbound_holding") or {}).get("latest", {}).get("trade_date"):
        gaps.append("北向持股未抓取成功或该股无北向数据")
    unlock = ownership.get("unlock_schedule") or {}
    if unlock.get("future_count") is None:
        gaps.append("限售解禁日程未抓取成功")
    gaps.append("监管/减持公告已提供标题和PDF链接，仍需人工复核公告原文实质内容")
    return gaps


@dataclass
class EngineFlags:
    # 对应投资决策引擎中的机械规则标记；None 表示样本不足或接口缺失。
    a2_deducted_profit_negative_2y: bool | None
    c3_ocf_low_3y: bool | None
    short_term_above_ma20_and_rising: bool | None
    middle_term_above_ma60_and_rising: bool | None
    chase_high_1m_over_80_pct: bool | None
    chase_high_1m_50_to_80_pct: bool | None
    big_down_day_over_5_pct: bool | None


def build_engine_flags(fin: dict[str, Any], tech: dict[str, Any]) -> EngineFlags:
    # 将财务和技术面快照转换成决策引擎可直接读取的布尔标记。
    change_20d = tech.get("change_20d_pct")
    day_pct = tech.get("day_pct_change")
    return EngineFlags(
        a2_deducted_profit_negative_2y=fin["checks"].get("deducted_net_profit_negative_latest_2y"),
        c3_ocf_low_3y=fin["checks"].get("ocf_to_net_profit_below_0_5_latest_3y"),
        short_term_above_ma20_and_rising=(
            None
            if tech.get("above_ma20") is None or tech.get("ma20_slope_up") is None
            else bool(tech["above_ma20"] and tech["ma20_slope_up"])
        ),
        middle_term_above_ma60_and_rising=(
            None
            if tech.get("above_ma60") is None or tech.get("ma60_slope_up") is None
            else bool(tech["above_ma60"] and tech["ma60_slope_up"])
        ),
        chase_high_1m_over_80_pct=None if change_20d is None else change_20d > 80,
        chase_high_1m_50_to_80_pct=None if change_20d is None else 50 <= change_20d <= 80,
        big_down_day_over_5_pct=None if day_pct is None else day_pct < -5,
    )


def build_payload(code: str, period: str) -> dict[str, Any]:
    # 主流程：依次抓行情、K 线、财务、估值、公告和持仓结构数据。
    quote = fetch_quote(code)
    klines = fetch_daily_klines(code)
    technical = build_technical_snapshot(klines)
    quote["trade_date"] = technical.get("last_date")
    financial_rows = fetch_financial_main(code)
    financial = financial_snapshot(financial_rows)
    valuation = valuation_snapshot(quote, klines, financial_rows)
    announcements = {
        "减持": fetch_announcements(code, "减持", 10),
        "监管": fetch_announcements(code, "监管 立案", 10),
        "回购": fetch_announcements(code, "回购", 10),
    }
    ownership = {
        "shareholder_count": fetch_shareholder_count(code),
        "margin_trading": fetch_margin_trading(code),
        "northbound_holding": fetch_northbound_holding(code),
        "unlock_schedule": fetch_unlock_schedule(code),
    }
    earnings_forecast = {
        "future_eps": None,
        "note": "未接入可靠一致预期源；决策引擎所需未来第3年EPS仍应由研报一致预期或人工假设填入。",
    }
    flags = build_engine_flags(financial, technical)
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "requested_period": period,
            "source": "Tencent quote/kline plus Eastmoney finance/announcement/ownership APIs",
        },
        "quote": quote,
        "technical": technical,
        "financial": financial,
        "valuation": valuation,
        "earnings_forecast": earnings_forecast,
        "announcements": announcements,
        "ownership": ownership,
        "engine_flags": asdict(flags),
    }
    payload["data_gaps_for_engine"] = build_data_gaps(payload)
    return payload


def print_summary(payload: dict[str, Any]) -> None:
    # 控制台摘要用于快速检查抓取结果；完整数据以 JSON 文件为准。
    quote = payload["quote"]
    tech = payload["technical"]
    latest = payload["financial"]["latest"] or {}
    flags = payload["engine_flags"]
    print(f"{quote.get('name')} {quote.get('code')} ({quote.get('market')})")
    print(f"price={quote.get('price')} pe_ttm={quote.get('pe_ttm')} pb={quote.get('pb')}")
    valuation = payload.get("valuation") or {}
    print(
        "valuation: "
        f"pe5y_median={valuation.get('pe_5y_median')} "
        f"pe5y_pct={valuation.get('pe_5y_percentile')}% "
        f"pb5y_pct={valuation.get('pb_5y_percentile')}%"
    )
    print(
        "tech: "
        f"close={tech.get('close')} ma20={tech.get('ma20')} ma60={tech.get('ma60')} "
        f"20d={tech.get('change_20d_pct')}% 60d={tech.get('change_60d_pct')}%"
    )
    print(
        "latest financial: "
        f"{latest.get('report')} revenue={round_or_none((latest.get('revenue_yuan') or 0) / 1e8)}亿 "
        f"np={round_or_none((latest.get('parent_net_profit_yuan') or 0) / 1e8)}亿 "
        f"deducted_np={round_or_none((latest.get('deducted_net_profit_yuan') or 0) / 1e8)}亿 "
        f"ocf/np={round_or_none(latest.get('ocf_to_net_profit'))}"
    )
    ownership = payload.get("ownership") or {}
    shareholder = (ownership.get("shareholder_count") or {}).get("latest") or {}
    margin = (ownership.get("margin_trading") or {}).get("latest") or {}
    northbound = (ownership.get("northbound_holding") or {}).get("latest") or {}
    unlock = ownership.get("unlock_schedule") or {}
    print(
        "ownership: "
        f"holders={shareholder.get('holder_num')}({shareholder.get('end_date')}) "
        f"margin_balance={round_or_none((margin.get('margin_balance_yuan') or 0) / 1e8)}亿({margin.get('date')}) "
        f"northbound={northbound.get('a_shares_ratio_pct')}%({northbound.get('trade_date')}) "
        f"future_unlocks={unlock.get('future_count')}"
    )
    print(f"mechanical flags: {json.dumps(flags, ensure_ascii=False)}")


def main() -> None:
    # 命令行入口：解析参数、生成 payload、写入 JSON，并打印摘要。
    parser = argparse.ArgumentParser(description="Fetch single-stock decision-engine data.")
    parser.add_argument("code", help="A-share stock code, e.g. 601138")
    parser.add_argument(
        "--period",
        choices=["short", "middle", "long"],
        default="middle",
        help="User intended holding period. Default: middle.",
    )
    parser.add_argument(
        "--out",
        help="Output JSON path. Default: data/stock_json/stock_data_<code>.json",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="Optional proxy URL, e.g. http://127.0.0.1:10809. "
        "By default system proxy env vars are ignored (Eastmoney is direct-reachable). "
        "Use this if your network requires a proxy.",
    )
    args = parser.parse_args()

    if args.proxy:
        # _EastmoneyProxy 是模块级变量，更新后 _build_session() 会自动使用它。
        globals()["_EastmoneyProxy"] = args.proxy

    code = args.code.strip()
    payload = build_payload(code, args.period)
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    default_out_dir = os.path.join(project_root, "data", "stock_json")
    os.makedirs(default_out_dir, exist_ok=True)
    out_path = args.out or os.path.join(default_out_dir, f"stock_data_{code}.json")
    with open(out_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    print_summary(payload)
    print(f"saved: {out_path}")


if __name__ == "__main__":
    main()
