from ..bedrock.bedrock_client import run_agent_loop
from ..tools.athena_cur_tools import get_cur_cost_by_service
from ..tools.cost_explorer_tools import get_cost_by_service

SYSTEM = """You are the Cost Analyst agent for AWS FinOps.
You analyse spend using Cost Explorer data. Be concise and specific.
Always reference real dollar figures and name the top cost drivers.
End with a "Suggestions:" section listing 2-3 concrete, specific cost-optimization
actions tied to the actual services driving spend (e.g. Savings Plans, idle resource
cleanup, storage tier changes) — not generic advice."""

TOOLS = [
    {
        "name": "get_cost_by_service",
        "description": "Get AWS cost broken down by service for a given month and region, from Cost Explorer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month like '2026-06' or 'JUN 26'"},
                "region": {"type": "string", "description": "AWS region, e.g. eu-central-1"},
            },
            "required": ["month"],
        },
    },
    {
        "name": "get_cur_cost_by_service",
        "description": (
            "Get the exact billed cost per service for a month straight from the Cost & "
            "Usage Report (Athena). More precise than Cost Explorer — use this when you want "
            "figures that reconcile exactly with the AWS Bills page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month like '2026-06' or 'JUN 26'"},
            },
            "required": ["month"],
        },
    },
]


async def _tool_runner(name, input_):
    params = input_ or {}
    if name == "get_cost_by_service":
        return await _get_cost_data(params.get("month"), params.get("region"))
    if name == "get_cur_cost_by_service":
        return await get_cur_cost_by_service(month=params.get("month"))
    return {"error": "unknown tool"}


async def _get_cost_data(month, region):
    """
    Prefer the CUR (Athena) total — it's the literal billed line items, so it
    matches the Bills page exactly. Falls back to Cost Explorer for months
    the CUR doesn't have data for yet (e.g. before billing_period partitioning
    was fixed), or for region-scoped views (CUR has no region column).
    """
    try:
        cur = await get_cur_cost_by_service(month=month)
        services = [
            {
                "service": s["service"],
                "amount": s["actual_cost"],
                "usageCost": s["usage_cost"],
                "actualCost": s["actual_cost"],
                "discount": s["discount"],
            }
            for s in cur["services"]
            if s["service"] != "TOTAL"
        ]
        if services:
            return {"period": {"Start": cur["billingPeriod"]}, "total": cur["totalCost"], "services": services, "source": "cur"}
    except Exception:  # noqa: BLE001
        pass  # CUR not available for this month yet — fall back to Cost Explorer
    ce = await get_cost_by_service(month=month, region=region)
    return {**ce, "source": "cost-explorer"}


async def run_cost_analyst(*, month, region=None):
    """Returns both raw data (for the UI) and a narrated summary (from Claude)."""
    data = await _get_cost_data(month, region)

    result = await run_agent_loop(
        system=SYSTEM,
        user_message=(
            f"Summarise the cost breakdown for {month} in {region}. Use the tool to fetch data. "
            "Then give 2-3 concrete cost-optimization suggestions based on the actual top services."
        ),
        tools=TOOLS,
        tool_runner=_tool_runner,
    )

    return {"summary": result["text"], "data": data, "trace": result["trace"]}