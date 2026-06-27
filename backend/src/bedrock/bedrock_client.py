import asyncio
import json
import os

import boto3

REGION = os.environ.get("AWS_REGION", "eu-central-1")

# Model IDs — Sonnet for analysis, Haiku for cheap routing.
# Override via env if Bedrock model strings differ in your account/region.
MODELS = {
    "SONNET": os.environ.get("BEDROCK_MODEL_SONNET", "anthropic.claude-sonnet-4-6"),
    "HAIKU": os.environ.get("BEDROCK_MODEL_HAIKU", "anthropic.claude-haiku-4-5"),
}

_client = boto3.client("bedrock-runtime", region_name=REGION)


def _invoke_claude_sync(model, system, messages, tools, max_tokens):
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        **({"system": system} if system else {}),
        "messages": messages,
        **({"tools": tools} if tools else {}),
    }

    res = _client.invoke_model(
        modelId=model,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    return json.loads(res["body"].read())


async def invoke_claude(
    *, model=None, system="", messages, tools=None, max_tokens=2048
):
    """Single-shot call to a Claude model on Bedrock.

    Credentials come from the EC2 instance role via IMDS — no keys needed.
    """
    model = model or MODELS["SONNET"]
    tools = tools or []
    return await asyncio.to_thread(
        _invoke_claude_sync, model, system, messages, tools, max_tokens
    )


async def run_agent_loop(
    *, model=None, system, user_message, tools, tool_runner, max_turns=6
):
    """Agentic loop: lets Claude call tools until it produces a final answer.

    `tool_runner(name, input)` must return the tool result (any JSON-serialisable).
    """
    model = model or MODELS["SONNET"]
    messages = [{"role": "user", "content": user_message}]

    for _ in range(max_turns):
        response = await invoke_claude(
            model=model, system=system, messages=messages, tools=tools
        )

        content = response.get("content") or []
        tool_uses = [b for b in content if b.get("type") == "tool_use"]

        if not tool_uses:
            # No tool calls — return the final text answer.
            text = "\n".join(b["text"] for b in content if b.get("type") == "text")
            return {"text": text, "raw": response}

        # Record the assistant turn, then run every requested tool.
        messages.append({"role": "assistant", "content": content})

        tool_results = []
        for tu in tool_uses:
            try:
                result = await tool_runner(tu["name"], tu.get("input"))
            except Exception as err:  # noqa: BLE001
                result = {"error": str(err)}
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": json.dumps(result),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return {"text": "Agent stopped after max turns.", "raw": None}
