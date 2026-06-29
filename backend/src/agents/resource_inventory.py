import json

from ..bedrock.bedrock_client import invoke_claude
from ..tools.resource_inventory_tools import get_full_inventory

SYSTEM = """You are the Resource Auditor agent.
You are given a full inventory of EVERY resource type this console can see in the
account/region: EC2 instances, EBS volumes, Elastic IPs, NAT Gateways, DynamoDB tables,
ElastiCache clusters, CloudFront distributions, SNS topics, SQS queues, RDS instances,
Lambda functions, ECS services, EKS clusters, ECR repositories (with their latest
image's vulnerability scan findings), S3 buckets, and load balancers. A heuristic pass
has already flagged some resources (public access, critical/high CVEs, desired/running
task mismatches, missing encryption, internet-facing schemes, unattached/unassociated
billed-but-idle resources) — treat those flags as a starting point, not the full
picture.

Review every resource in the data you were given — every category, not just the
flagged ones — and call submit_findings exactly once with concrete, specific fixes.
For ECR images with vulnerabilities, name the severity counts and recommend a
remediation path (rebuild from a patched base image, pin/upgrade the vulnerable
package, etc.) — never invent a CVE that isn't in the data. For unattached EBS
volumes / unassociated Elastic IPs, call out that they're still billed while idle. For
other resources, use judgment beyond the heuristic flags where the data supports it
(e.g. an oversized Lambda memory/timeout setting, a non-Multi-AZ RDS instance, an ECS
service with no running tasks, a NAT Gateway that could be replaced by a VPC endpoint
for S3/DynamoDB traffic). Never invent a finding the data doesn't support — if nothing
of substance stands out for a category, say so briefly instead of padding."""

TOOLS = [
    {
        "name": "submit_findings",
        "description": "Submit your final resource audit. Call this exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "resource": {"type": "string", "description": "Resource id/name from the inventory"},
                            "type": {
                                "type": "string",
                                "description": "ec2 | ebs | eip | nat | dynamodb | elasticache | cloudfront | sns | sqs | rds | lambda | ecs | eks | ecr | s3 | elb",
                            },
                            "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                            "issue": {"type": "string", "description": "What's wrong, with concrete figures (CVE counts, task counts, etc.)"},
                            "suggestion": {"type": "string", "description": "Concrete fix"},
                        },
                        "required": ["resource", "type", "severity", "issue", "suggestion"],
                    },
                },
            },
            "required": ["findings"],
        },
    }
]


def _digest_resources(resources):
    # Trim to fields Claude needs; cap volume so the prompt stays bounded on large accounts.
    trimmed = [
        {k: r[k] for k in ("type", "id", "detail", "status", "flags") if k in r}
        for r in resources[:250]
    ]
    return trimmed


async def run_resource_inventory(*, region=None):
    inventory = await get_full_inventory(region=region)
    resources = inventory["resources"]

    findings = []
    summary_text = ""

    if resources or inventory["errors"]:
        user_message = f"""Region: {region}.
Resource counts: {json.dumps(inventory["counts"])}.
Category errors (service likely disabled or missing permission — don't treat as a finding): {json.dumps(inventory["errors"], default=str)}.
Full inventory: {json.dumps(_digest_resources(resources), default=str)}.
Heuristically flagged resources (cross-check, not authoritative): {json.dumps(_digest_resources(inventory["flagged"]), default=str)}.

Review this and call submit_findings with your audit."""

        response = await invoke_claude(
            system=SYSTEM,
            messages=[{"role": "user", "content": user_message}],
            tools=TOOLS,
            max_tokens=3500,
        )
        content = response.get("content") or []
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == "submit_findings":
                findings = (block.get("input") or {}).get("findings", [])
        summary_text = "\n".join(b["text"] for b in content if b.get("type") == "text")

    return {
        "summary": summary_text,
        "resources": resources,
        "counts": inventory["counts"],
        "errors": inventory["errors"],
        "findings": findings,
        "trace": [],
    }
