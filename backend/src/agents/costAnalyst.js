import { runAgentLoop } from "../bedrock/bedrockClient.js";
import { getCostByService } from "../tools/costExplorerTools.js";

const SYSTEM = `You are the Cost Analyst agent for AWS FinOps.
You analyse spend using Cost Explorer data. Be concise and specific.
Always reference real dollar figures and name the top cost drivers.`;

const TOOLS = [
  {
    name: "get_cost_by_service",
    description: "Get AWS cost broken down by service for a given month and region.",
    input_schema: {
      type: "object",
      properties: {
        month: { type: "string", description: "Month like '2026-06' or 'JUN 26'" },
        region: { type: "string", description: "AWS region, e.g. eu-central-1" },
      },
      required: ["month"],
    },
  },
];

/** Returns both raw data (for the UI) and a narrated summary (from Claude). */
export async function runCostAnalyst({ month, region }) {
  const data = await getCostByService({ month, region });

  const { text } = await runAgentLoop({
    system: SYSTEM,
    userMessage: `Summarise the cost breakdown for ${month} in ${region}. Use the tool to fetch data.`,
    tools: TOOLS,
    toolRunner: async (name, input) =>
      name === "get_cost_by_service" ? getCostByService(input) : { error: "unknown tool" },
  });

  return { summary: text, data };
}
