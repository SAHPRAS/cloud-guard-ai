from ..bedrock.bedrock_client import run_agent_loop
from ..tools.cloudwatch_tools import get_ec2_cpu_utilization
from ..tools.cost_explorer_tools import get_cost_by_service
from ..tools.ec2_tools import list_ec2_instances

SYSTEM = """You are the Rightsizing agent.
You spot over-provisioned EC2 / EKS / DocumentDB resources and recommend cheaper sizing.
Use the tools to list running EC2 instances and check their real CPU utilisation before
recommending anything — never guess a recommendation from spend alone.
Present your output as a "Suggestions:" section — each entry must state the resource,
its actual measured CPU utilisation, current vs suggested size, and estimated monthly saving."""

TOOLS = [
    {
        "name": "list_ec2_instances",
        "description": "List running EC2 instances in a region (id, type, state, name tag).",
        "input_schema": {
            "type": "object",
            "properties": {"region": {"type": "string", "description": "AWS region, e.g. eu-central-1"}},
        },
    },
    {
        "name": "get_ec2_cpu_utilization",
        "description": "Get average/max CPU utilisation % for an EC2 instance over the trailing N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
                "region": {"type": "string"},
                "days": {"type": "integer", "description": "Lookback window, default 14"},
            },
            "required": ["instance_id"],
        },
    },
]


async def _tool_runner(name, input_):
    params = input_ or {}
    if name == "list_ec2_instances":
        return await list_ec2_instances(region=params.get("region"))
    if name == "get_ec2_cpu_utilization":
        return await get_ec2_cpu_utilization(
            instance_id=params.get("instance_id"), region=params.get("region"), days=params.get("days", 14)
        )
    return {"error": "unknown tool"}


async def run_rightsizing(*, month, region=None):
    cost = await get_cost_by_service(month=month, region=region)
    top_services = cost["services"][:6]

    result = await run_agent_loop(
        system=SYSTEM,
        user_message=(
            f"Top services by spend in {region} for {month}: {top_services}. "
            "If EC2 is a notable cost, list running instances and check CPU utilisation on the "
            "largest/most numerous ones, then suggest concrete rightsizing actions with estimated "
            "monthly savings, citing the actual CPU numbers you found."
        ),
        tools=TOOLS,
        tool_runner=_tool_runner,
    )

    return {"summary": result["text"], "topServices": top_services, "trace": result["trace"]}
