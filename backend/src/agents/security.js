import { invokeClaude } from "../bedrock/bedrockClient.js";
import {
  getSecurityHubFindings,
  getGuardDutyFindings,
} from "../tools/securityTools.js";

const SYSTEM = `You are the Security agent.
You review SecurityHub and GuardDuty findings and surface the most important risks.
Prioritise: public S3 buckets, IAM wildcard policies, open security groups, GuardDuty alerts.
For each finding give a one-line remediation. Be direct about severity.`;

export async function runSecurity() {
  const [sh, gd] = await Promise.all([
    getSecurityHubFindings({ max: 25 }),
    getGuardDutyFindings({ max: 25 }),
  ]);

  const findings = [
    ...(Array.isArray(sh) ? sh : []),
    ...(Array.isArray(gd) ? gd : []),
  ];

  const res = await invokeClaude({
    system: SYSTEM,
    messages: [
      {
        role: "user",
        content: `Findings: ${JSON.stringify(
          findings.slice(0, 20)
        )}. Summarise the critical ones and give remediation steps.`,
      },
    ],
    maxTokens: 800,
  });

  const counts = findings.reduce(
    (a, f) => {
      a.total++;
      if (f.severity === "crit") a.critical++;
      else if (f.severity === "warn") a.medium++;
      else a.low++;
      return a;
    },
    { total: 0, critical: 0, medium: 0, low: 0 }
  );

  return {
    summary: res.content?.find((b) => b.type === "text")?.text || "",
    findings,
    counts,
  };
}
