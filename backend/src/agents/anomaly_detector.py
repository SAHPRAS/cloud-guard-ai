import json

from ..bedrock.bedrock_client import invoke_claude
from ..tools.cost_explorer_tools import get_monthly_trend

SYSTEM = """You are the Anomaly Detector agent.
Given a monthly cost series, identify months where spend deviates sharply from the trend.
Return concise findings: what spiked, by how much, and the likely cause."""


async def run_anomaly_detector(*, region=None):
    """Simple statistical pass + Claude narration."""
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

    res = await invoke_claude(
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Monthly trend: {json.dumps(trend)}. "
                    f"Flagged: {json.dumps(findings)}. Explain each spike briefly."
                ),
            }
        ],
        max_tokens=600,
    )

    content = res.get("content") or []
    summary = next((b["text"] for b in content if b.get("type") == "text"), "")

    return {"summary": summary, "findings": findings, "trend": trend}