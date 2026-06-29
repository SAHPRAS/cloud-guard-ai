import json

from ..bedrock.bedrock_client import run_agent_loop
from ..tools.cost_explorer_tools import get_cost_by_service, get_monthly_trend

SYSTEM = """You are the Anomaly Detector agent.
You decide which months in the trend are real anomalies — a heuristic pass below flags
candidates by raw % change, but that's a hint, not a verdict. Use your own judgment on
the shape of the trend: a one-off spike, a sustained step-change, and normal seasonal
variation should be treated differently. You may dismiss a flagged month if it doesn't
look like a genuine anomaly, and you may flag a month the heuristic missed if the shape
looks wrong.

For every month you consider a genuine anomaly, call get_cost_by_service on it to find
which service actually drove the change before you explain it — never guess the cause
from the aggregate trend alone.

Call submit_anomalies exactly once with your final list. Each entry needs a concrete fix
suggestion to investigate the root cause or prevent recurrence (e.g. a budget alarm, a
tagging gap to close, a specific service/resource to audit) — never a generic platitude."""

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
    {
        "name": "submit_anomalies",
        "description": "Submit your final, judged list of real anomalies. Call this exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "anomalies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "month": {"type": "string"},
                            "changePct": {"type": "number"},
                            "severity": {"type": "string", "enum": ["crit", "warn"]},
                            "driverService": {"type": "string", "description": "The service that actually drove the change"},
                            "explanation": {"type": "string"},
                            "suggestion": {"type": "string"},
                        },
                        "required": ["month", "severity", "explanation", "suggestion"],
                    },
                },
            },
            "required": ["anomalies"],
        },
    },
]


async def run_anomaly_detector(*, region=None):
    trend = await get_monthly_trend(months=12, region=region)

    # Heuristic pass on month-over-month change — a cross-check hint for Claude,
    # not the authoritative answer (Claude makes the final call via submit_anomalies).
    hinted = []
    for i in range(1, len(trend)):
        prev = trend[i - 1]["amount"]
        cur = trend[i]["amount"]
        if prev > 0:
            change = (cur - prev) / prev
            if change > 0.25:
                hinted.append({"month": trend[i]["month"], "changePct": round(change * 100)})

    ai_anomalies = []

    async def _tool_runner(name, input_):
        nonlocal ai_anomalies
        if name == "get_cost_by_service":
            params = input_ or {}
            return await get_cost_by_service(month=params.get("month"), region=params.get("region"))
        if name == "submit_anomalies":
            ai_anomalies = (input_ or {}).get("anomalies", [])
            return {"ok": True}
        return {"error": "unknown tool"}

    result = await run_agent_loop(
        system=SYSTEM,
        user_message=f"""Region: {region}. Monthly trend (last 12 months): {json.dumps(trend)}.
Heuristic candidates (>25% MoM change — cross-check only, you decide what's real): {json.dumps(hinted)}.
Investigate any month you judge to be a genuine anomaly, then call submit_anomalies.""",
        tools=TOOLS,
        tool_runner=_tool_runner,
    )

    if ai_anomalies:
        findings = [
            {
                "month": a.get("month"),
                "changePct": a.get("changePct"),
                "severity": a.get("severity", "warn"),
                "driverService": a.get("driverService"),
                "explanation": a.get("explanation"),
                "suggestion": a.get("suggestion"),
            }
            for a in ai_anomalies
        ]
    else:
        # Claude didn't call submit_anomalies (e.g. hit max turns) — fall back to the
        # heuristic candidates so the UI still shows something rather than nothing.
        findings = [{**h, "severity": "warn", "explanation": None, "suggestion": None} for h in hinted]

    return {
        "summary": result["text"],
        "findings": findings,
        "trend": trend,
        "aiGenerated": bool(ai_anomalies),
        "trace": result["trace"],
    }
