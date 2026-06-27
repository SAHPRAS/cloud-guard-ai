import asyncio
import json

from ..bedrock.bedrock_client import invoke_claude
from ..tools.security_tools import get_guard_duty_findings, get_security_hub_findings

SYSTEM = """You are the Security agent.
You review SecurityHub and GuardDuty findings and surface the most important risks.
Prioritise: public S3 buckets, IAM wildcard policies, open security groups, GuardDuty alerts.
For each finding give a one-line remediation. Be direct about severity."""


async def run_security():
    sh, gd = await asyncio.gather(
        get_security_hub_findings(max=25),
        get_guard_duty_findings(max=25),
    )

    findings = (sh if isinstance(sh, list) else []) + (gd if isinstance(gd, list) else [])

    res = await invoke_claude(
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Findings: {json.dumps(findings[:20])}. Summarise the critical ones and give remediation steps.",
            }
        ],
        max_tokens=800,
    )

    content = res.get("content") or []
    summary = next((b["text"] for b in content if b.get("type") == "text"), "")

    counts = {"total": 0, "critical": 0, "medium": 0, "low": 0}
    for f in findings:
        counts["total"] += 1
        if f.get("severity") == "crit":
            counts["critical"] += 1
        elif f.get("severity") == "warn":
            counts["medium"] += 1
        else:
            counts["low"] += 1

    return {"summary": summary, "findings": findings, "counts": counts}