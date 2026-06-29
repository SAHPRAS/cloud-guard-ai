from ..bedrock.bedrock_client import run_agent_loop
from ..tools.security_tools import get_guard_duty_findings, get_security_hub_findings

SYSTEM = """You are the Security agent.
Call the tools to fetch SecurityHub and GuardDuty findings, then surface the most
important risks. Prioritise: public S3 buckets, IAM wildcard policies, open security
groups, GuardDuty alerts. If the first batch looks incomplete or you need more context
on severity distribution, you may call a tool again with a higher max.
Present remediations as a "Suggestions:" section — one concrete, actionable line per
finding. Be direct about severity."""

TOOLS = [
    {
        "name": "get_security_hub_findings",
        "description": "Fetch active SecurityHub findings.",
        "input_schema": {
            "type": "object",
            "properties": {"max": {"type": "integer", "description": "Max findings to fetch, default 25"}},
        },
    },
    {
        "name": "get_guard_duty_findings",
        "description": "Fetch active GuardDuty findings.",
        "input_schema": {
            "type": "object",
            "properties": {"max": {"type": "integer", "description": "Max findings to fetch, default 25"}},
        },
    },
]


async def run_security():
    collected = []

    async def _tool_runner(name, input_):
        params = input_ or {}
        max_results = params.get("max", 25)
        if name == "get_security_hub_findings":
            res = await get_security_hub_findings(max=max_results)
        elif name == "get_guard_duty_findings":
            res = await get_guard_duty_findings(max=max_results)
        else:
            return {"error": "unknown tool"}
        if isinstance(res, list):
            collected.extend(res)
        return res

    result = await run_agent_loop(
        system=SYSTEM,
        user_message="Fetch SecurityHub and GuardDuty findings, then summarise the critical ones with remediation steps.",
        tools=TOOLS,
        tool_runner=_tool_runner,
    )

    counts = {"total": 0, "critical": 0, "medium": 0, "low": 0}
    for f in collected:
        counts["total"] += 1
        if f.get("severity") == "crit":
            counts["critical"] += 1
        elif f.get("severity") == "warn":
            counts["medium"] += 1
        else:
            counts["low"] += 1

    return {"summary": result["text"], "findings": collected, "counts": counts, "trace": result["trace"]}
