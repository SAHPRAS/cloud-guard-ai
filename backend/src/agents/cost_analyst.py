from ..bedrock.bedrock_client import run_agent_loop
from ..tools.cost_explorer_tools import get_cost_by_service

SYSTEM = """You are the Cost Analyst agent for AWS FinOps.
You analyse spend using Cost Explorer data. Be concise and specific.
Always reference real dollar figures and name the top cost drivers."""

TOOLS = [
    {
        "name": "get_cost_by_service",
        "description": "Get AWS cost broken down by service for a given month and region.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month like '2026-06' or 'JUN 26'"},
                "region": {"type": "string", "description": "AWS region, e.g. eu-central-1"},
            },
            "required": ["month"],
        },
    },
]


async def _tool_runner(name, input_):
    if name == "get_cost_by_service":
        return await get_cost_by_service(**(input_ or {}))
    return {"error": "unknown tool"}


async def run_cost_analyst(*, month, region=None):
    """Returns both raw data (for the UI) and a narrated summary (from Claude)."""
    data = await get_cost_by_service(month=month, region=region)

    result = await run_agent_loop(
        system=SYSTEM,
        user_message=f"Summarise the cost breakdown for {month} in {region}. Use the tool to fetch data.",
        tools=TOOLS,
        tool_runner=_tool_runner,
    )

    return {"summary": result["text"], "data": data}