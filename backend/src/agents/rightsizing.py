import json

from ..bedrock.bedrock_client import invoke_claude
from ..tools.cost_explorer_tools import get_cost_by_service

SYSTEM = """You are the Rightsizing agent.
You spot over-provisioned EC2 / EKS / DocumentDB resources and recommend cheaper sizing.
Each recommendation must state the resource, current vs suggested size, and monthly saving.
Note: in production, pull utilisation from CloudWatch (CPU, memory) and EC2/EKS describe APIs."""


async def run_rightsizing(*, month, region=None):
    # In a full build, fetch CloudWatch utilisation + describe instances here.
    cost = await get_cost_by_service(month=month, region=region)
    top_services = cost["services"][:6]

    res = await invoke_claude(
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Top services by spend: {json.dumps(top_services)}. "
                    "Suggest concrete rightsizing actions with estimated monthly savings."
                ),
            }
        ],
        max_tokens=700,
    )

    content = res.get("content") or []
    summary = next((b["text"] for b in content if b.get("type") == "text"), "")

    return {"summary": summary, "topServices": top_services}