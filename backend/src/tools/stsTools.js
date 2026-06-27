import { STSClient, GetCallerIdentityCommand } from "@aws-sdk/client-sts";

const REGION = process.env.AWS_REGION || "eu-central-1";
const sts = new STSClient({ region: REGION });

/** Who am I — powers the profile/account/role bar in the UI. */
export async function getCallerIdentity() {
  try {
    const res = await sts.send(new GetCallerIdentityCommand({}));
    // Assumed-role ARN looks like:
    // arn:aws:sts::211125387793:assumed-role/UnITe-Admin/session
    const arn = res.Arn || "";
    const roleMatch = arn.match(/assumed-role\/([^/]+)/);
    return {
      account: res.Account,
      role: roleMatch ? roleMatch[1] : "instance-role",
      arn,
      profile: process.env.PROFILE_LABEL || "DT_DTRD_DEV",
      region: REGION,
    };
  } catch (e) {
    return { error: String(e.message), profile: process.env.PROFILE_LABEL || "unknown" };
  }
}
