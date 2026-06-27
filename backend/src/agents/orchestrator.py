from ..bedrock.bedrock_client import MODELS, invoke_claude

SYSTEM = """You are the orchestrator for Cloud Guard AI, an AWS operations console.
Classify the user's request into exactly one category and reply with ONLY that word:
- COST          (spend breakdowns, what am I paying for)
- ANOMALY       (spikes, unusual usage)
- RIGHTSIZING   (oversized / underused resources, savings)
- FORECAST      (future / projected spend)
- SECURITY      (IAM, S3, security groups, GuardDuty, findings)
Reply with the single category word and nothing else."""

VALID = ["COST", "ANOMALY", "RIGHTSIZING", "FORECAST", "SECURITY"]


async def classify_intent(query):
    res = await invoke_claude(
        model=MODELS["HAIKU"],  # cheap + fast for routing
        system=SYSTEM,
        messages=[{"role": "user", "content": query}],
        max_tokens=16,
    )
    content = res.get("content") or [{}]
    word = (content[0].get("text") or "COST").strip().upper()
    return word.lower() if word in VALID else "cost"