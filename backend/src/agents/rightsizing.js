import { invokeClaude } from "../bedrock/bedrockClient.js";
import { getCostByService } from "../tools/costExplorerTools.js";

const SYSTEM = `You are the Rightsizing agent.
You spot over-provisioned EC2 / EKS / DocumentDB resources and recommend cheaper sizing.
Each recommendation must state the resource, current vs suggested size, and monthly saving.
Note: in production, pull utilisation from CloudWatch (CPU, memory) and EC2/EKS describe APIs.`;

export async function runRightsizing({ month, region }) {
  // In a full build, fetch CloudWatch utilisation + describe instances here.
  const cost = await getCostByService({ month, region });

  const res = await invokeClaude({
    system: SYSTEM,
    messages: [
      {
        role: "user",
        content: `Top services by spend: ${JSON.stringify(
          cost.services.slice(0, 6)
        )}. Suggest concrete rightsizing actions with estimated monthly savings.`,
      },
    ],
    maxTokens: 700,
  });

  return {
    summary: res.content?.find((b) => b.type === "text")?.text || "",
    topServices: cost.services.slice(0, 6),
  };
}
