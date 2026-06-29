import json

from ..bedrock.bedrock_client import run_agent_loop
from ..tools.cost_explorer_tools import (
    forecast_by_service,
    get_cost_forecast,
    get_monthly_trend,
    get_service_trend,
    growth_model_forecast,
)

SYSTEM = """You are the Forecasting agent.
You project future AWS spend from historical actuals, broken down by service.

You are given a deterministic compound-growth projection and AWS's own ML forecast as
reference points — but you must not just copy them. Reason about each driver service:
does its growth rate look like it will hold, accelerate, or taper off? Are any services
volatile or seasonal in the history you were given? Use the get_service_trend tool if you
need more months of history on a specific service before deciding.

Confidence should decrease the further into the future the forecast extends, and should
drop further for services with volatile/inconsistent history.

Call submit_forecast exactly once with your final structured projection (total, range,
confidence, and a per-service breakdown). After that, give a short closing explanation of
the main drivers and end with a "Suggestions:" section giving 1-2 concrete actions to
control or reduce the projected spend before it's incurred."""

TOOLS = [
    {
        "name": "get_service_trend",
        "description": "Get N months of historical cost per service, to verify a specific service's growth rate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "months": {"type": "integer", "description": "How many trailing months, e.g. 12"},
                "region": {"type": "string"},
            },
        },
    },
    {
        "name": "submit_forecast",
        "description": "Submit your final structured forecast. Call this exactly once, after you've reasoned about the trend data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "total": {"type": "number", "description": "Projected total spend for the target month."},
                "low": {"type": "number"},
                "high": {"type": "number"},
                "confidence": {"type": "integer", "description": "0-100"},
                "services": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "projected": {"type": "number"},
                            "low": {"type": "number"},
                            "high": {"type": "number"},
                            "driver": {"type": "string", "description": "1-line reason for this trajectory"},
                        },
                        "required": ["service", "projected"],
                    },
                },
            },
            "required": ["total", "services"],
        },
    },
]


async def run_forecasting(*, month, region=None):
    trend = await get_monthly_trend(months=24, region=region)
    aws = await get_cost_forecast(month=month, region=region)  # AWS ML total forecast
    model = growth_model_forecast(trend, month)  # explainable total model (cross-check)
    by_service = await forecast_by_service(month=month, region=region)  # deterministic per-service cross-check

    ai_forecast = {}

    async def _tool_runner(name, input_):
        nonlocal ai_forecast
        if name == "get_service_trend":
            params = input_ or {}
            return await get_service_trend(months=params.get("months", 12), region=params.get("region"))
        if name == "submit_forecast":
            ai_forecast = input_ or {}
            return {"ok": True}
        return {"error": "unknown tool"}

    result = await run_agent_loop(
        system=SYSTEM,
        user_message=f"""Forecast spend for {month} in {region}.
Trailing 24-month total trend: {json.dumps(trend)}.
Deterministic per-service growth-model projection (cross-check only): {json.dumps(by_service["services"][:8])}.
Deterministic total cross-check: ${by_service["total"]}.
AWS ML forecast (cross-check only): {json.dumps(aws)}.
Reason about each driver service's trajectory, then call submit_forecast with your own
projection, then explain the top drivers and give 1-2 concrete suggestions.""",
        tools=TOOLS,
        tool_runner=_tool_runner,
    )

    months_ahead = by_service["monthsAhead"]
    has_ai = bool(ai_forecast.get("services"))

    if has_ai:
        services = [
            {
                "service": s.get("service"),
                "projected": round(s.get("projected", 0)),
                "low": round(s.get("low", s.get("projected", 0) * 0.88)),
                "high": round(s.get("high", s.get("projected", 0) * 1.12)),
                "driver": s.get("driver", ""),
            }
            for s in ai_forecast["services"]
        ]
        services.sort(key=lambda s: s["projected"], reverse=True)
        total = round(ai_forecast.get("total", sum(s["projected"] for s in services)))
        low = round(ai_forecast.get("low", total * 0.88))
        high = round(ai_forecast.get("high", total * 1.12))
        confidence = ai_forecast.get("confidence")
        if confidence is None:
            confidence = max(40, 92 - months_ahead * 7)
    else:
        # Claude didn't call submit_forecast (e.g. hit max turns) — fall back to the
        # deterministic growth-model projection so the UI still gets a number.
        services = by_service["services"]
        total, low, high = by_service["total"], by_service["low"], by_service["high"]
        confidence = max(40, 92 - months_ahead * 7)

    return {
        "summary": result["text"],
        "aiGenerated": has_ai,
        # structured data for the per-service table/chart in the UI:
        "services": services,
        "total": total,
        "low": low,
        "high": high,
        "monthsAhead": months_ahead,
        "confidence": confidence,
        "trendHistory": trend,  # for the chart: actuals leading up to the projection
        "crossCheck": {"aws": aws, "growthModel": model},
        "trace": result["trace"],
    }
