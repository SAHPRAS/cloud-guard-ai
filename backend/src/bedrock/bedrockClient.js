import {
  BedrockRuntimeClient,
  InvokeModelCommand,
} from "@aws-sdk/client-bedrock-runtime";

const REGION = process.env.AWS_REGION || "eu-central-1";

// Model IDs — Sonnet for analysis, Haiku for cheap routing.
// Override via env if Bedrock model strings differ in your account/region.
export const MODELS = {
  SONNET: process.env.BEDROCK_MODEL_SONNET || "anthropic.claude-sonnet-4-6",
  HAIKU: process.env.BEDROCK_MODEL_HAIKU || "anthropic.claude-haiku-4-5",
};

const client = new BedrockRuntimeClient({ region: REGION });

/**
 * Single-shot call to a Claude model on Bedrock.
 * Credentials come from the EC2 instance role via IMDS — no keys needed.
 */
export async function invokeClaude({
  model = MODELS.SONNET,
  system = "",
  messages,
  tools = [],
  maxTokens = 2048,
}) {
  const body = {
    anthropic_version: "bedrock-2023-05-31",
    max_tokens: maxTokens,
    ...(system ? { system } : {}),
    messages,
    ...(tools.length ? { tools } : {}),
  };

  const command = new InvokeModelCommand({
    modelId: model,
    contentType: "application/json",
    accept: "application/json",
    body: JSON.stringify(body),
  });

  const res = await client.send(command);
  return JSON.parse(Buffer.from(res.body).toString());
}

/**
 * Agentic loop: lets Claude call tools until it produces a final answer.
 * `toolRunner(name, input)` must return the tool result (any JSON-serialisable).
 */
export async function runAgentLoop({
  model = MODELS.SONNET,
  system,
  userMessage,
  tools,
  toolRunner,
  maxTurns = 6,
}) {
  const messages = [{ role: "user", content: userMessage }];

  for (let turn = 0; turn < maxTurns; turn++) {
    const response = await invokeClaude({ model, system, messages, tools });

    const toolUses = (response.content || []).filter(
      (b) => b.type === "tool_use"
    );

    if (toolUses.length === 0) {
      // No tool calls — return the final text answer.
      const text = (response.content || [])
        .filter((b) => b.type === "text")
        .map((b) => b.text)
        .join("\n");
      return { text, raw: response };
    }

    // Record the assistant turn, then run every requested tool.
    messages.push({ role: "assistant", content: response.content });

    const toolResults = [];
    for (const tu of toolUses) {
      let result;
      try {
        result = await toolRunner(tu.name, tu.input);
      } catch (err) {
        result = { error: String(err?.message || err) };
      }
      toolResults.push({
        type: "tool_result",
        tool_use_id: tu.id,
        content: JSON.stringify(result),
      });
    }
    messages.push({ role: "user", content: toolResults });
  }

  return { text: "Agent stopped after max turns.", raw: null };
}
