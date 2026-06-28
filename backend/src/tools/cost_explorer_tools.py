import asyncio
import re
import time
from datetime import date

import boto3

# Cost Explorer is a global service but the SDK requires a region.
_ce = boto3.client("ce", region_name="us-east-1")

# ---- simple in-memory cache (swap for Redis/ElastiCache later) ----
_cache = {}
_TTL_S = 60 * 60  # 1h


def _cache_get(key):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]
    return None


def _cache_set(key, value):
    _cache[key] = (time.time(), value)


_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def month_to_range(month):
    """Convert "JUN 26" / "2026-06" into a Cost Explorer date window."""
    if re.match(r"^\d{4}-\d{2}$", month):
        year, mon = (int(x) for x in month.split("-"))
    else:
        m, y = month.upper().split(" ")
        mon = _MONTH_MAP[m]
        year = 2000 + int(y)

    start = f"{year}-{mon:02d}-01"
    if mon == 12:
        nxt = f"{year + 1}-01-01"
    else:
        nxt = f"{year}-{mon + 1:02d}-01"
    return {"Start": start, "End": nxt}


def _region_filter(region):
    if region and region != "ALL REGIONS":
        return {"Dimensions": {"Key": "REGION", "Values": [region.split(" ")[0]]}}
    return None


def _get_cost_by_service_sync(month, region):
    period = month_to_range(month)
    filt = _region_filter(region)

    kwargs = dict(
        TimePeriod=period,
        Granularity="MONTHLY",
        Metrics=["NetAmortizedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    if filt:
        kwargs["Filter"] = filt

    res = _ce.get_cost_and_usage(**kwargs)
    groups = (res.get("ResultsByTime") or [{}])[0].get("Groups") or []
    services = sorted(
        (
            {"service": g["Keys"][0], "amount": float(g["Metrics"]["NetAmortizedCost"]["Amount"])}
            for g in groups
        ),
        key=lambda s: s["amount"],
        reverse=True,
    )
    services = [s for s in services if s["amount"] > 0]
    total = sum(s["amount"] for s in services)
    return {"period": period, "total": round(total, 2), "services": services}


async def get_cost_by_service(*, month, region=None):
    key = f"svc:{month}:{region}"
    cached = _cache_get(key)
    if cached:
        return cached
    out = await asyncio.to_thread(_get_cost_by_service_sync, month, region)
    _cache_set(key, out)
    return out


def _get_cost_breakdown_sync(month, region):
    """Per-service cost split into its RECORD_TYPE components (Usage, Discount,
    Credit, RIFee, Tax, ...) — mirrors the "expand a service" view on the Bills page."""
    period = month_to_range(month)
    filt = _region_filter(region)

    kwargs = dict(
        TimePeriod=period,
        Granularity="MONTHLY",
        Metrics=["NetAmortizedCost"],
        GroupBy=[
            {"Type": "DIMENSION", "Key": "SERVICE"},
            {"Type": "DIMENSION", "Key": "RECORD_TYPE"},
        ],
    )
    if filt:
        kwargs["Filter"] = filt

    res = _ce.get_cost_and_usage(**kwargs)
    groups = (res.get("ResultsByTime") or [{}])[0].get("Groups") or []

    by_service = {}
    for g in groups:
        service, record_type = g["Keys"]
        amount = round(float(g["Metrics"]["NetAmortizedCost"]["Amount"]), 2)
        if amount == 0:
            continue
        by_service.setdefault(service, []).append({"type": record_type, "amount": amount})

    services = [
        {
            "service": service,
            "total": round(sum(c["amount"] for c in components), 2),
            "components": sorted(components, key=lambda c: c["amount"], reverse=True),
        }
        for service, components in by_service.items()
    ]
    services = [s for s in services if s["total"] > 0]
    services.sort(key=lambda s: s["total"], reverse=True)
    return {"period": period, "services": services}


async def get_cost_breakdown(*, month, region=None):
    key = f"breakdown:{month}:{region}"
    cached = _cache_get(key)
    if cached:
        return cached
    out = await asyncio.to_thread(_get_cost_breakdown_sync, month, region)
    _cache_set(key, out)
    return out


_DIAGNOSTIC_METRICS = ["UnblendedCost", "NetUnblendedCost", "BlendedCost", "AmortizedCost", "NetAmortizedCost"]


def _record_type_breakdown_sync(period, metric):
    res = _ce.get_cost_and_usage(
        TimePeriod=period,
        Granularity="MONTHLY",
        Metrics=[metric],
        GroupBy=[{"Type": "DIMENSION", "Key": "RECORD_TYPE"}],
    )
    groups = (res.get("ResultsByTime") or [{}])[0].get("Groups") or []
    return {g["Keys"][0]: round(float(g["Metrics"][metric]["Amount"]), 2) for g in groups}


def _get_cost_diagnostics_sync(month):
    period = month_to_range(month)

    totals = {}
    for metric in _DIAGNOSTIC_METRICS:
        res = _ce.get_cost_and_usage(TimePeriod=period, Granularity="MONTHLY", Metrics=[metric])
        totals[metric] = round(float(res["ResultsByTime"][0]["Total"][metric]["Amount"]), 2)

    return {
        "period": period,
        "totals": totals,
        "recordTypeBreakdown": {
            "UnblendedCost": _record_type_breakdown_sync(period, "UnblendedCost"),
            "NetAmortizedCost": _record_type_breakdown_sync(period, "NetAmortizedCost"),
        },
    }


async def get_cost_diagnostics(*, month):
    """Compare every CE cost metric + RECORD_TYPE breakdown — used to reconcile against the Bills page total."""
    return await asyncio.to_thread(_get_cost_diagnostics_sync, month)


def _month_window(months):
    today = date.today().replace(day=1)
    end = today
    y, m = end.year, end.month - months
    while m <= 0:
        m += 12
        y -= 1
    start = date(y, m, 1)
    return start, end


def _get_monthly_trend_sync(months, region):
    start, end = _month_window(months)
    filt = _region_filter(region)

    kwargs = dict(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY",
        Metrics=["NetAmortizedCost"],
    )
    if filt:
        kwargs["Filter"] = filt

    res = _ce.get_cost_and_usage(**kwargs)
    return [
        {
            "month": r["TimePeriod"]["Start"][:7],
            "amount": round(float(r["Total"]["NetAmortizedCost"]["Amount"])),
        }
        for r in res.get("ResultsByTime") or []
    ]


async def get_monthly_trend(*, months=12, region=None):
    """Trailing N months of monthly totals — feeds the forecast model."""
    return await asyncio.to_thread(_get_monthly_trend_sync, months, region)


def _get_cost_forecast_sync(month, region):
    period = month_to_range(month)
    filt = _region_filter(region)

    kwargs = dict(
        TimePeriod=period,
        Granularity="MONTHLY",
        Metric="NET_AMORTIZED_COST",
        PredictionIntervalLevel=80,
    )
    if filt:
        kwargs["Filter"] = filt

    res = _ce.get_cost_forecast(**kwargs)
    mean = float(res["Total"]["Amount"])
    by_time = res.get("ForecastResultsByTime") or [{}]
    lo = float(by_time[0].get("PredictionIntervalLowerBound", mean * 0.88))
    hi = float(by_time[0].get("PredictionIntervalUpperBound", mean * 1.12))
    return {
        "projected": round(mean),
        "low": round(lo),
        "high": round(hi),
        "source": "ce:GetCostForecast",
    }


async def get_cost_forecast(*, month, region=None):
    """AWS-native ML forecast for a future window."""
    try:
        return await asyncio.to_thread(_get_cost_forecast_sync, month, region)
    except Exception:  # noqa: BLE001
        # CE forecast can fail without enough history — fall back to trend model.
        trend = await get_monthly_trend(months=12, region=region)
        return growth_model_forecast(trend, month)


def _get_service_trend_sync(months, region):
    start, end = _month_window(months)
    filt = _region_filter(region)

    kwargs = dict(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY",
        Metrics=["NetAmortizedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    if filt:
        kwargs["Filter"] = filt

    res = _ce.get_cost_and_usage(**kwargs)
    results = res.get("ResultsByTime") or []
    month_labels = []
    series = {}
    for i, rt in enumerate(results):
        month_labels.append(rt["TimePeriod"]["Start"][:7])
        for g in rt.get("Groups") or []:
            svc = g["Keys"][0]
            amt = float(g["Metrics"]["NetAmortizedCost"]["Amount"])
            if svc not in series:
                series[svc] = [0] * len(results)
            series[svc][i] = amt

    return {"months": month_labels, "series": series}


async def get_service_trend(*, months=6, region=None):
    """
    Trailing N months of cost grouped by service AND month.
    Returns { "months":[...], "series": { service: [amt per month] } }.
    This is what feeds the per-service forecast.
    """
    key = f"svctrend:{months}:{region}"
    cached = _cache_get(key)
    if cached:
        return cached
    out = await asyncio.to_thread(_get_service_trend_sync, months, region)
    _cache_set(key, out)
    return out


async def forecast_by_service(*, month, region=None):
    """
    Forecast EACH service forward to a target future month, then sum.
    Per-service compound growth from its own history; total = sum of services.
    Returns { "services":[{service, projected, low, high}], "total", "monthsAhead" }.
    """
    trend = await get_service_trend(months=6, region=region)
    hist, series = trend["months"], trend["series"]
    if not hist:
        return {"services": [], "total": 0, "monthsAhead": 0}

    target = month_to_range(month)["Start"][:7]
    ty, tm = (int(x) for x in target.split("-"))
    ly, lm = (int(x) for x in hist[-1].split("-"))
    ahead = max(1, (ty - ly) * 12 + (tm - lm))

    services = []
    for service, arr in series.items():
        last = arr[-1] if arr else 0
        g, n = 0.0, 0
        for i in range(1, len(arr)):
            if arr[i - 1] > 0:
                g += arr[i] / arr[i - 1] - 1
                n += 1
        rate = g / n if n else 0.04
        projected = last * (1 + rate) ** ahead
        if projected > 0:
            services.append(
                {
                    "service": service,
                    "projected": round(projected),
                    "low": round(projected * 0.88),
                    "high": round(projected * 1.12),
                    "ratePct": round(rate * 1000) / 10,
                }
            )

    services.sort(key=lambda s: s["projected"], reverse=True)
    total = sum(s["projected"] for s in services)
    return {
        "services": services,
        "total": round(total),
        "low": round(total * 0.9),
        "high": round(total * 1.1),
        "monthsAhead": ahead,
    }


def growth_model_forecast(trend, month):
    """Compound-growth fallback the Forecasting agent can also use to explain "why"."""
    if not trend:
        return {"projected": 0, "low": 0, "high": 0, "source": "growth-model"}

    last = trend[-1]["amount"]
    growth, n = 0.0, 0
    for i in range(1, len(trend)):
        if trend[i - 1]["amount"] > 0:
            growth += trend[i]["amount"] / trend[i - 1]["amount"] - 1
            n += 1
    rate = growth / n if n else 0.04

    target = month_to_range(month)["Start"][:7]
    ty, tm = (int(x) for x in target.split("-"))
    ly, lm = (int(x) for x in trend[-1]["month"].split("-"))
    ahead = (ty - ly) * 12 + (tm - lm)

    projected = round(last * (1 + rate) ** max(1, ahead))
    return {
        "projected": projected,
        "low": round(projected * 0.88),
        "high": round(projected * 1.12),
        "ratePct": round(rate * 1000) / 10,
        "monthsAhead": ahead,
        "source": "growth-model",
    }