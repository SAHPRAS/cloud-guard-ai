import { invokeClaude, MODELS } from "../bedrock/bedrockClient.js";

const SYSTEM = `You are the orchestrator for Cloud Guard AI, an AWS operations console.
Classify the user's request into exactly one category and reply with ONLY that word:
- COST          (spend breakdowns, what am I paying for)
- ANOMALY       (spikes, unusual usage)
- RIGHTSIZING   (oversized / underused resources, savings)
- FORECAST      (future / projected spend)
- SECURITY      (IAM, S3, security groups, GuardDuty, findings)
Reply with the single category word and nothing else.`;

export async function classifyIntent(query) {
  const res = await invokeClaude({
    model: MODELS.HAIKU, // cheap + fast for routing
    system: SYSTEM,
    messages: [{ role: "user", content: query }],
    maxTokens: 16,
  });
  const word = (res.content?.[0]?.text || "COST").trim().toUpperCase();
  const valid = ["COST", "ANOMALY", "RIGHTSIZING", "FORECAST", "SECURITY"];
  return valid.includes(word) ? word.toLowerCase() : "cost";
}
