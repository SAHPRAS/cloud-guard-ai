import json

from ..bedrock.bedrock_client import run_agent_loop
from ..tools.cost_explorer_tools import get_cost_by_service, get_monthly_trend

SYSTEM = """You are the Anomaly Detector agent.
Given a monthly cost series, identify months where spend deviates sharply from the trend.
A statistical pass has already flagged candidate months by % change — use the
get_cost_by_service tool on a flagged month to find which specific service actually drove
the spike before explaining it. Don't guess the cause from the aggregate trend alone.
For each flagged spike, end with a "Suggestions:" section giving one concrete action to
investigate the root cause or prevent recurrence (e.g. a budget alarm, tagging gap to fix,
a specific service to audit)."""

TOOLS = [
    {
        "name": "get_cost_by_service",
        "description": "Get AWS cost broken down by service for a given month and region.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month like '2026-06'"},
                "region": {"type": "string", "description": "AWS region, e.g. eu-central-1"},
            },
            "required": ["month"],
        },
    },
]


async def _tool_runner(name, input_):
    if name == "get_cost_by_service":
        params = input_ or {}
        return await get_cost_by_service(month=params.get("month"), region=params.get("region"))
    return {"error": "unknown tool"}


async def run_anomaly_detector(*, region=None):
    """Statistical pass (Python) flags candidate spikes; Claude investigates + narrates."""
    trend = await get_monthly_trend(months=12, region=region)

    # z-score style flagging on month-over-month change
    findings = []
    for i in range(1, len(trend)):
        prev = trend[i - 1]["amount"]
        cur = trend[i]["amount"]
        if prev > 0:
            change = (cur - prev) / prev
            if change > 0.25:
                findings.append(
                    {
                        "month": trend[i]["month"],
                        "changePct": round(change * 100),
                        "delta": cur - prev,
                        "severity": "crit" if change > 0.6 else "warn",
                    }
                )

    result = await run_agent_loop(
        system=SYSTEM,
        user_message=(
            f"Region: {region}. Monthly trend: {json.dumps(trend)}. "
            f"Flagged: {json.dumps(findings)}. For each flagged month, call the tool to break "
            "it down by service, explain what actually drove the spike, and give a concrete "
            "suggestion to investigate or prevent it."
        ),
        tools=TOOLS,
        tool_runner=_tool_runner,
    )

    return {"summary": result["text"], "findings": findings, "trend": trend, "trace": result["trace"]}
