import json

from ..bedrock.bedrock_client import invoke_claude

SYSTEM = """You are the Synthesizer — the final agent in the Cloud Guard AI pipeline.
You receive the output every other agent already produced for this scan (cost, anomaly,
rightsizing, forecast, security) and your job is to reason ACROSS them, not repeat them.

Find the connections a single-domain agent would miss: does a cost spike line up with a
security finding (e.g. a misconfigured resource left running)? Does a rightsizing
candidate explain part of a forecasted increase? Is a security risk on the same service
that's also the top spend driver?

Call submit_synthesis exactly once with your conclusions. Be specific and cite real
figures/resource names from the data you were given — never invent numbers. If two
agents' data don't meaningfully connect, that's fine — say so briefly and focus on the
single highest-value insight instead of padding."""

TOOLS = [
    {
        "name": "submit_synthesis",
        "description": "Submit your final cross-agent analysis. Call this exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "headline": {
                    "type": "string",
                    "description": "One-sentence executive takeaway for this scan.",
                },
                "narrative": {
                    "type": "string",
                    "description": "2-4 sentence analysis connecting findings across agents.",
                },
                "priorities": {
                    "type": "array",
                    "description": "Ranked list of the most important actions, highest priority first.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Short action title, e.g. 'Resize idle DocDB cluster'"},
                            "category": {"type": "string", "enum": ["cost", "security", "rightsizing", "forecast", "anomaly"]},
                            "impact": {"type": "string", "enum": ["high", "medium", "low"]},
                            "detail": {"type": "string", "description": "1-2 sentences: why this matters, with a concrete figure."},
                        },
                        "required": ["title", "category", "impact", "detail"],
                    },
                },
            },
            "required": ["headline", "narrative", "priorities"],
        },
    }
]


def _block_digest(key, block):
    if not isinstance(block, dict):
        return None
    if block.get("error"):
        return {"agent": key, "error": block["error"]}
    digest = {"agent": key, "summary": block.get("summary")}
    if key == "cost" and block.get("data"):
        digest["total"] = block["data"].get("total")
        digest["topServices"] = block["data"].get("services", [])[:5]
        digest["comparison"] = block.get("comparison")
    elif key == "anomaly":
        digest["findings"] = block.get("findings")
    elif key == "rightsizing":
        digest["topServices"] = block.get("topServices")
    elif key == "forecast":
        digest["total"] = block.get("total")
        digest["confidence"] = block.get("confidence")
        digest["topServices"] = (block.get("services") or [])[:5]
    elif key == "security":
        digest["counts"] = block.get("counts")
        digest["findings"] = (block.get("findings") or [])[:10]
    return digest


async def run_synthesis(*, blocks, month, region=None, mode="live"):
    """One Bedrock call that reasons across every agent's results from this scan."""
    digests = [d for d in (_block_digest(k, v) for k, v in blocks.items()) if d]
    if not digests:
        return None

    user_message = f"""Scan context: {month} in {region}, mode={mode}.
Agent outputs from this scan:
{json.dumps(digests, default=str)}

Find the cross-cutting insight(s) and submit your synthesis."""

    response = await invoke_claude(
        system=SYSTEM,
        messages=[{"role": "user", "content": user_message}],
        tools=TOOLS,
        max_tokens=1500,
    )

    content = response.get("content") or []
    for block in content:
        if block.get("type") == "tool_use" and block.get("name") == "submit_synthesis":
            data = block.get("input") or {}
            return {
                "headline": data.get("headline", ""),
                "narrative": data.get("narrative", ""),
                "priorities": data.get("priorities", []),
            }

    text = "\n".join(b["text"] for b in content if b.get("type") == "text")
    return {"headline": "", "narrative": text, "priorities": []}
