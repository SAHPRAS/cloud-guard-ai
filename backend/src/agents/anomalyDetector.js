import { invokeClaude } from "../bedrock/bedrockClient.js";
import { getMonthlyTrend } from "../tools/costExplorerTools.js";

const SYSTEM = `You are the Anomaly Detector agent.
Given a monthly cost series, identify months where spend deviates sharply from the trend.
Return concise findings: what spiked, by how much, and the likely cause.`;

/** Simple statistical pass + Claude narration. */
export async function runAnomalyDetector({ region }) {
  const trend = await getMonthlyTrend({ months: 12, region });

  // z-score style flagging on month-over-month change
  const findings = [];
  for (let i = 1; i < trend.length; i++) {
    const prev = trend[i - 1].amount;
    const cur = trend[i].amount;
    if (prev > 0) {
      const change = (cur - prev) / prev;
      if (change > 0.25) {
        findings.push({
          month: trend[i].month,
          changePct: Math.round(change * 100),
          delta: cur - prev,
          severity: change > 0.6 ? "crit" : "warn",
        });
      }
    }
  }

  const res = await invokeClaude({
    system: SYSTEM,
    messages: [
      {
        role: "user",
        content: `Monthly trend: ${JSON.stringify(trend)}. Flagged: ${JSON.stringify(
          findings
        )}. Explain each spike briefly.`,
      },
    ],
    maxTokens: 600,
  });

  return {
    summary: res.content?.find((b) => b.type === "text")?.text || "",
    findings,
    trend,
  };
}
