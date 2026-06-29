from ..bedrock.bedrock_client import run_agent_loop
from ..tools.athena_cur_tools import get_cur_cost_by_service
from ..tools.cloudwatch_tools import get_ec2_cpu_utilization
from ..tools.cost_explorer_tools import (
    forecast_by_service,
    get_cost_by_service,
    get_cost_forecast,
    get_monthly_trend,
    get_service_trend,
)
from ..tools.ec2_tools import list_ec2_instances
from ..tools.security_tools import get_guard_duty_findings, get_security_hub_findings

SYSTEM = """You are the Cloud Guard AI assistant — a single agent with access to every
read-only AWS tool this console has: cost (Cost Explorer + CUR), spend trends and
forecasts, EC2 inventory and utilisation, and security findings.

Answer the user's question by calling whichever tools you need, in whatever order makes
sense, across as many turns as it takes. Cross-domain questions (e.g. "why did my bill
go up and is anything insecure?") may require calling tools from more than one area —
do that rather than answering only part of the question.

Always cite concrete figures and resource IDs from tool results, never invent numbers.
End with a "Suggestions:" section of concrete next actions where relevant."""

TOOLS = [
    {
        "name": "get_cost_by_service",
        "description": "Cost Explorer: cost per service for a given month and region.",
        "input_schema": {
            "type": "object",
            "properties": {"month": {"type": "string"}, "region": {"type": "string"}},
            "required": ["month"],
        },
    },
    {
        "name": "get_cur_cost_by_service",
        "description": "Exact billed cost per service for a month, straight from the CUR (Athena) — reconciles with the Bills page.",
        "input_schema": {
            "type": "object",
            "properties": {"month": {"type": "string"}},
            "required": ["month"],
        },
    },
    {
        "name": "get_monthly_trend",
        "description": "Trailing N months of total spend.",
        "input_schema": {
            "type": "object",
            "properties": {"months": {"type": "integer"}, "region": {"type": "string"}},
        },
    },
    {
        "name": "get_service_trend",
        "description": "Trailing N months of spend broken down by service.",
        "input_schema": {
            "type": "object",
            "properties": {"months": {"type": "integer"}, "region": {"type": "string"}},
        },
    },
    {
        "name": "get_cost_forecast",
        "description": "AWS-native ML forecast of total spend for a future/current month.",
        "input_schema": {
            "type": "object",
            "properties": {"month": {"type": "string"}, "region": {"type": "string"}},
            "required": ["month"],
        },
    },
    {
        "name": "forecast_by_service",
        "description": "Per-service forecast for a future/current month, summed to a total.",
        "input_schema": {
            "type": "object",
            "properties": {"month": {"type": "string"}, "region": {"type": "string"}},
            "required": ["month"],
        },
    },
    {
        "name": "list_ec2_instances",
        "description": "List running EC2 instances (id, type, state, name tag).",
        "input_schema": {"type": "object", "properties": {"region": {"type": "string"}}},
    },
    {
        "name": "get_ec2_cpu_utilization",
        "description": "Average/max CPU utilisation % for an EC2 instance over the trailing N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
                "region": {"type": "string"},
                "days": {"type": "integer"},
            },
            "required": ["instance_id"],
        },
    },
    {
        "name": "get_security_hub_findings",
        "description": "Active SecurityHub findings.",
        "input_schema": {"type": "object", "properties": {"max": {"type": "integer"}}},
    },
    {
        "name": "get_guard_duty_findings",
        "description": "Active GuardDuty findings.",
        "input_schema": {"type": "object", "properties": {"max": {"type": "integer"}}},
    },
]


async def _tool_runner(name, input_):
    p = input_ or {}
    if name == "get_cost_by_service":
        return await get_cost_by_service(month=p.get("month"), region=p.get("region"))
    if name == "get_cur_cost_by_service":
        return await get_cur_cost_by_service(month=p.get("month"))
    if name == "get_monthly_trend":
        return await get_monthly_trend(months=p.get("months", 12), region=p.get("region"))
    if name == "get_service_trend":
        return await get_service_trend(months=p.get("months", 6), region=p.get("region"))
    if name == "get_cost_forecast":
        return await get_cost_forecast(month=p.get("month"), region=p.get("region"))
    if name == "forecast_by_service":
        return await forecast_by_service(month=p.get("month"), region=p.get("region"))
    if name == "list_ec2_instances":
        return await list_ec2_instances(region=p.get("region"))
    if name == "get_ec2_cpu_utilization":
        return await get_ec2_cpu_utilization(
            instance_id=p.get("instance_id"), region=p.get("region"), days=p.get("days", 14)
        )
    if name == "get_security_hub_findings":
        return await get_security_hub_findings(max=p.get("max", 25))
    if name == "get_guard_duty_findings":
        return await get_guard_duty_findings(max=p.get("max", 25))
    return {"error": "unknown tool"}


async def run_assistant(*, query, month, region=None):
    result = await run_agent_loop(
        system=SYSTEM,
        user_message=(
            f"Current month context: {month}, region: {region}. "
            f"User question: {query}"
        ),
        tools=TOOLS,
        tool_runner=_tool_runner,
        max_turns=8,
    )
    return {"summary": result["text"], "trace": result["trace"]}
