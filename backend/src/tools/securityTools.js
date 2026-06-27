import {
  SecurityHubClient,
  GetFindingsCommand,
} from "@aws-sdk/client-securityhub";
import {
  GuardDutyClient,
  ListDetectorsCommand,
  ListFindingsCommand,
  GetFindingsCommand as GdGetFindings,
} from "@aws-sdk/client-guardduty";

const REGION = process.env.AWS_REGION || "eu-central-1";
const sh = new SecurityHubClient({ region: REGION });
const gd = new GuardDutyClient({ region: REGION });

const SEV_MAP = { CRITICAL: "crit", HIGH: "crit", MEDIUM: "warn", LOW: "ok", INFORMATIONAL: "ok" };

/** Active SecurityHub findings, normalised for the UI. */
export async function getSecurityHubFindings({ max = 25 } = {}) {
  try {
    const cmd = new GetFindingsCommand({
      Filters: {
        RecordState: [{ Value: "ACTIVE", Comparison: "EQUALS" }],
        WorkflowStatus: [{ Value: "NEW", Comparison: "EQUALS" }],
      },
      MaxResults: max,
    });
    const res = await sh.send(cmd);
    return (res.Findings || []).map((f) => ({
      title: f.Title,
      desc: f.Description?.slice(0, 160),
      severity: SEV_MAP[f.Severity?.Label] || "warn",
      resource: f.Resources?.[0]?.Id,
      type: f.Types?.[0],
    }));
  } catch (e) {
    return { error: String(e.message), hint: "Enable SecurityHub in this region" };
  }
}

/** Active GuardDuty findings. */
export async function getGuardDutyFindings({ max = 25 } = {}) {
  try {
    const dets = await gd.send(new ListDetectorsCommand({}));
    const detectorId = dets.DetectorIds?.[0];
    if (!detectorId) return [];
    const list = await gd.send(
      new ListFindingsCommand({ DetectorId: detectorId, MaxResults: max })
    );
    if (!list.FindingIds?.length) return [];
    const det = await gd.send(
      new GdGetFindings({ DetectorId: detectorId, FindingIds: list.FindingIds })
    );
    return (det.Findings || []).map((f) => ({
      title: f.Type,
      desc: f.Description?.slice(0, 160),
      severity: f.Severity >= 7 ? "crit" : f.Severity >= 4 ? "warn" : "ok",
      resource: f.Resource?.ResourceType,
    }));
  } catch (e) {
    return { error: String(e.message), hint: "Enable GuardDuty in this region" };
  }
}
