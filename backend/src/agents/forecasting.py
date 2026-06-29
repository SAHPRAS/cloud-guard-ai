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
Explain the main drivers and state confidence honestly.
Confidence should decrease the further into the future the forecast extends.
If you want more history on a specific driver service to justify its growth rate, use the
get_service_trend tool to pull additional months before finalising your explanation.
End with a "Suggestions:" section giving 1-2 concrete actions to control or reduce the
projected spend before it's incurred (e.g. commit to a Savings Plan, set a budget alarm
on the fastest-growing service, review a specific service's growth rate)."""

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
]


async def _tool_runner(name, input_):
    if name == "get_service_trend":
        params = input_ or {}
        return await get_service_trend(months=params.get("months", 12), region=params.get("region"))
    return {"error": "unknown tool"}


async def run_forecasting(*, month, region=None):
    trend = await get_monthly_trend(months=24, region=region)
    aws = await get_cost_forecast(month=month, region=region)  # AWS ML total forecast
    model = growth_model_forecast(trend, month)  # explainable total model
    by_service = await forecast_by_service(month=month, region=region)  # per-service + sum

    result = await run_agent_loop(
        system=SYSTEM,
        user_message=f"""Forecast spend for {month} in {region}.
Per-service projection: {json.dumps(by_service["services"][:8])}.
Projected total: ${by_service["total"]}.
AWS forecast: {json.dumps(aws)}.
Give a final projection, the top driver services, a range, a confidence %, and 1-2
concrete suggestions to control the projected spend.""",
        tools=TOOLS,
        tool_runner=_tool_runner,
    )

    confidence = max(40, 92 - by_service["monthsAhead"] * 7)

    return {
        "summary": result["text"],
        # structured data for the per-service table in the UI:
        "services": by_service["services"],  # [{service, projected, low, high}]
        "total": by_service["total"],
        "low": by_service["low"],
        "high": by_service["high"],
        "monthsAhead": by_service["monthsAhead"],
        "confidence": confidence,
        "aws": aws,
        "model": model,
        "trace": result["trace"],
    }
