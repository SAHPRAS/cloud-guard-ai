import json

from ..bedrock.bedrock_client import invoke_claude
from ..tools.cost_explorer_tools import (
    forecast_by_service,
    get_cost_forecast,
    get_monthly_trend,
    growth_model_forecast,
)

SYSTEM = """You are the Forecasting agent.
You project future AWS spend from historical actuals, broken down by service.
Explain the main drivers and state confidence honestly.
Confidence should decrease the further into the future the forecast extends."""


async def run_forecasting(*, month, region=None):
    trend = await get_monthly_trend(months=24, region=region)
    aws = await get_cost_forecast(month=month, region=region)  # AWS ML total forecast
    model = growth_model_forecast(trend, month)  # explainable total model
    by_service = await forecast_by_service(month=month, region=region)  # per-service + sum

    res = await invoke_claude(
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"""Forecast spend for {month}.
Per-service projection: {json.dumps(by_service["services"][:8])}.
Projected total: ${by_service["total"]}.
AWS forecast: {json.dumps(aws)}.
Give a final projection, the top driver services, a range, and a confidence %.""",
            }
        ],
        max_tokens=700,
    )

    content = res.get("content") or []
    summary = next((b["text"] for b in content if b.get("type") == "text"), "")

    confidence = max(40, 92 - by_service["monthsAhead"] * 7)

    return {
        "summary": summary,
        # structured data for the per-service table in the UI:
        "services": by_service["services"],  # [{service, projected, low, high}]
        "total": by_service["total"],
        "low": by_service["low"],
        "high": by_service["high"],
        "monthsAhead": by_service["monthsAhead"],
        "confidence": confidence,
        "aws": aws,
        "model": model,
    }