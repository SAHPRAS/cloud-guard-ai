import asyncio
import os

import boto3

REGION = os.environ.get("AWS_REGION", "eu-central-1")

_SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL", "UNDEFINED"]


def _region_code(region):
    if not region or region == "ALL REGIONS":
        return REGION
    return region.split(" ")[0]


def _client(service, region):
    return boto3.client(service, region_name=_region_code(region))


# ---------- EC2 (instances, EBS volumes, Elastic IPs, NAT gateways) ----------

def _list_ec2_instances_sync(region):
    client = _client("ec2", region)
    # Every instance except terminated ones (those no longer exist/cost anything) —
    # running AND stopped/stopping/pending, so idle-but-not-cleaned-up instances surface too.
    res = client.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
    )
    out = []
    for reservation in res.get("Reservations") or []:
        for inst in reservation.get("Instances") or []:
            name = next((t["Value"] for t in inst.get("Tags") or [] if t["Key"] == "Name"), inst["InstanceId"])
            state = inst["State"]["Name"]
            out.append(
                {
                    "type": "ec2",
                    "id": inst["InstanceId"],
                    "name": name,
                    "detail": f"{inst['InstanceType']} · {inst.get('Placement', {}).get('AvailabilityZone', '')}",
                    "status": state,
                    "flags": (["stopped-still-billed-for-ebs"] if state == "stopped" else []),
                    "severity": ("low" if state == "stopped" else None),
                }
            )
    return out


async def list_ec2_instances_for_inventory(*, region=None):
    """EC2 instances, running or stopped (excludes terminated). Feeds resource inventory — rightsizing's CPU check is a separate, deeper pass on running instances only."""
    try:
        return await asyncio.to_thread(_list_ec2_instances_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check ec2:DescribeInstances permission for this region"}


def _list_ebs_volumes_sync(region):
    client = _client("ec2", region)
    out = []
    for vol in client.describe_volumes().get("Volumes") or []:
        name = next((t["Value"] for t in vol.get("Tags") or [] if t["Key"] == "Name"), vol["VolumeId"])
        unattached = len(vol.get("Attachments") or []) == 0
        out.append(
            {
                "type": "ebs",
                "id": vol["VolumeId"],
                "name": name,
                "detail": f"{vol.get('VolumeType')} · {vol.get('Size')}GB",
                "status": vol.get("State"),  # AWS native: "available" (unattached) | "in-use" | "creating" | ...
                "flags": (["unattached-still-billed"] if unattached else []),
                "severity": ("medium" if unattached else None),
            }
        )
    return out


async def list_ebs_volumes(*, region=None):
    """EBS volumes — flags unattached volumes (still billed, doing nothing). Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_ebs_volumes_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check ec2:DescribeVolumes permission for this region"}


def _list_elastic_ips_sync(region):
    client = _client("ec2", region)
    out = []
    for addr in client.describe_addresses().get("Addresses") or []:
        unassociated = not addr.get("AssociationId")
        out.append(
            {
                "type": "eip",
                "id": addr.get("AllocationId", addr.get("PublicIp")),
                "name": addr.get("PublicIp"),
                "detail": f"domain {addr.get('Domain')}" + (f" · attached to {addr.get('InstanceId')}" if addr.get("InstanceId") else ""),
                "status": "associated" if not unassociated else "unassociated",
                "flags": (["unassociated-still-billed"] if unassociated else []),
                "severity": ("medium" if unassociated else None),
            }
        )
    return out


async def list_elastic_ips(*, region=None):
    """Elastic IPs — flags unassociated EIPs (billed hourly when not attached to a running instance). Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_elastic_ips_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check ec2:DescribeAddresses permission for this region"}


def _list_nat_gateways_sync(region):
    client = _client("ec2", region)
    out = []
    for nat in client.describe_nat_gateways(Filter=[{"Name": "state", "Values": ["available"]}]).get("NatGateways") or []:
        out.append(
            {
                "type": "nat",
                "id": nat["NatGatewayId"],
                "name": nat["NatGatewayId"],
                "detail": f"vpc {nat.get('VpcId')} · subnet {nat.get('SubnetId')}",
                "status": nat.get("State"),
                "flags": [],
                "severity": None,
            }
        )
    return out


async def list_nat_gateways(*, region=None):
    """NAT Gateways — flat hourly cost regardless of traffic, easy to forget. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_nat_gateways_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check ec2:DescribeNatGateways permission for this region"}


# ---------- Security Groups ----------

_SENSITIVE_PORTS = {
    21: "FTP", 22: "SSH", 23: "Telnet", 1433: "MSSQL", 3306: "MySQL",
    3389: "RDP", 5432: "PostgreSQL", 5900: "VNC", 6379: "Redis",
    9200: "Elasticsearch", 27017: "MongoDB",
}


def _rule_open_to_world(perm):
    if any(r.get("CidrIp") == "0.0.0.0/0" for r in perm.get("IpRanges") or []):
        return True
    if any(r.get("CidrIpv6") == "::/0" for r in perm.get("Ipv6Ranges") or []):
        return True
    return False


def _rule_risk(perm):
    """(severity, description) for an ingress rule open to the world, or (None, None) if it isn't."""
    if not _rule_open_to_world(perm):
        return None, None
    proto = perm.get("IpProtocol")
    from_port, to_port = perm.get("FromPort"), perm.get("ToPort")
    if proto == "-1" or (from_port == 0 and to_port == 65535):
        return "high", "ALL ports/protocols open to the internet (0.0.0.0/0)"
    if from_port is not None and to_port is not None:
        hits = [name for port, name in _SENSITIVE_PORTS.items() if from_port <= port <= to_port]
        if hits:
            return "high", f"{', '.join(hits)} open to the internet (port {from_port}-{to_port})"
        return "medium", f"port {from_port}-{to_port}/{proto} open to the internet"
    return "medium", f"{proto} open to the internet"


def _list_security_groups_sync(region):
    client = _client("ec2", region)
    out = []
    for sg in client.describe_security_groups().get("SecurityGroups") or []:
        risky_rules = []
        worst = None
        for perm in sg.get("IpPermissions") or []:
            sev, desc = _rule_risk(perm)
            if sev:
                risky_rules.append(desc)
                if sev == "high":
                    worst = "high"
                elif worst != "high":
                    worst = "medium"
        out.append(
            {
                "type": "sg",
                "id": sg["GroupId"],
                "name": sg.get("GroupName", sg["GroupId"]),
                "detail": (sg.get("Description") or "no description") + (f" · vpc {sg.get('VpcId')}" if sg.get("VpcId") else ""),
                "status": "open-to-internet" if risky_rules else "restricted",
                "flags": risky_rules,
                "severity": worst,
            }
        )
    return out


async def list_security_groups(*, region=None):
    """Security groups — flags ingress rules open to 0.0.0.0/0 / ::/0; severity 'high' for sensitive ports or all-traffic, 'medium' for other open ports. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_security_groups_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check ec2:DescribeSecurityGroups permission for this region"}


# ---------- DynamoDB ----------

def _list_dynamodb_sync(region):
    client = _client("dynamodb", region)
    out = []
    for name in (client.list_tables().get("TableNames") or [])[:60]:
        t = client.describe_table(TableName=name)["Table"]
        billing = (t.get("BillingModeSummary") or {}).get("BillingMode", "PROVISIONED")
        out.append(
            {
                "type": "dynamodb",
                "id": name,
                "name": name,
                "detail": f"{billing} · {t.get('ItemCount', 0)} items · {round((t.get('TableSizeBytes') or 0) / 1e6, 1)}MB",
                "status": t.get("TableStatus"),
                "flags": [],
                "severity": None,
            }
        )
    return out


async def list_dynamodb_tables(*, region=None):
    """DynamoDB tables — billing mode, item count, size. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_dynamodb_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check dynamodb:ListTables/DescribeTable permission for this region"}


# ---------- ElastiCache ----------

def _list_elasticache_sync(region):
    client = _client("elasticache", region)
    out = []
    for c in client.describe_cache_clusters().get("CacheClusters") or []:
        out.append(
            {
                "type": "elasticache",
                "id": c["CacheClusterId"],
                "name": c["CacheClusterId"],
                "detail": f"{c.get('Engine')} · {c.get('CacheNodeType')} · {c.get('NumCacheNodes')} node(s)",
                "status": c.get("CacheClusterStatus"),
                "flags": [],
                "severity": None,
            }
        )
    return out


async def list_elasticache_clusters(*, region=None):
    """ElastiCache clusters — engine, node type/count. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_elasticache_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check elasticache:DescribeCacheClusters permission for this region"}


# ---------- CloudFront (global) ----------

def _list_cloudfront_sync():
    client = boto3.client("cloudfront", region_name=REGION)
    out = []
    for d in (client.list_distributions().get("DistributionList") or {}).get("Items") or []:
        out.append(
            {
                "type": "cloudfront",
                "id": d["Id"],
                "name": (d.get("Aliases") or {}).get("Items", [d["DomainName"]])[0] if (d.get("Aliases") or {}).get("Items") else d["DomainName"],
                "detail": f"{'enabled' if d.get('Enabled') else 'disabled'} · {d.get('PriceClass')}",
                "status": d.get("Status"),
                "flags": ([] if d.get("Enabled") else ["disabled-still-listed"]),
                "severity": (None if d.get("Enabled") else "low"),
            }
        )
    return out


async def list_cloudfront_distributions():
    """CloudFront distributions (global). Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_cloudfront_sync)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check cloudfront:ListDistributions permission"}


# ---------- SNS / SQS ----------

def _list_sns_sync(region):
    client = _client("sns", region)
    out = []
    for t in client.list_topics().get("Topics") or []:
        arn = t["TopicArn"]
        out.append({"type": "sns", "id": arn, "name": arn.split(":")[-1], "detail": arn, "status": "active", "flags": [], "severity": None})
    return out


async def list_sns_topics(*, region=None):
    """SNS topics. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_sns_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check sns:ListTopics permission for this region"}


def _list_sqs_sync(region):
    client = _client("sqs", region)
    out = []
    for url in client.list_queues().get("QueueUrls") or []:
        name = url.split("/")[-1]
        out.append({"type": "sqs", "id": url, "name": name, "detail": url, "status": "active", "flags": [], "severity": None})
    return out


async def list_sqs_queues(*, region=None):
    """SQS queues. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_sqs_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check sqs:ListQueues permission for this region"}


# ---------- RDS ----------

def _list_rds_sync(region):
    client = _client("rds", region)
    res = client.describe_db_instances()
    out = []
    for db in res.get("DBInstances") or []:
        out.append(
            {
                "type": "rds",
                "id": db["DBInstanceIdentifier"],
                "name": db["DBInstanceIdentifier"],
                "detail": f"{db.get('Engine')} {db.get('EngineVersion', '')} · {db.get('DBInstanceClass')}",
                "status": db.get("DBInstanceStatus"),
                "publiclyAccessible": db.get("PubliclyAccessible", False),
                "multiAz": db.get("MultiAZ", False),
                "storageGb": db.get("AllocatedStorage"),
                "flags": (["publicly-accessible"] if db.get("PubliclyAccessible") else []),
                "severity": ("high" if db.get("PubliclyAccessible") else None),
            }
        )
    return out


async def list_rds_instances(*, region=None):
    """RDS instances — engine, class, status, public-access flag. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_rds_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check rds:DescribeDBInstances permission for this region"}


# ---------- Lambda ----------

def _list_lambda_sync(region):
    client = _client("lambda", region)
    out = []
    paginator = client.get_paginator("list_functions")
    for page in paginator.paginate():
        for fn in page.get("Functions") or []:
            out.append(
                {
                    "type": "lambda",
                    "id": fn["FunctionName"],
                    "name": fn["FunctionName"],
                    "detail": f"{fn.get('Runtime', 'n/a')} · {fn.get('MemorySize')}MB · timeout {fn.get('Timeout')}s",
                    "status": fn.get("State", "Active"),
                    "lastModified": fn.get("LastModified"),
                    "flags": [],
                    "severity": None,
                }
            )
            if len(out) >= 200:
                return out
    return out


async def list_lambda_functions(*, region=None):
    """Lambda functions — runtime, memory, timeout. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_lambda_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check lambda:ListFunctions permission for this region"}


# ---------- ECS ----------

def _list_ecs_sync(region):
    client = _client("ecs", region)
    out = []
    cluster_arns = client.list_clusters().get("clusterArns") or []
    for cluster_arn in cluster_arns:
        cluster_name = cluster_arn.split("/")[-1]
        service_arns = client.list_services(cluster=cluster_arn).get("serviceArns") or []
        if not service_arns:
            continue
        services = client.describe_services(cluster=cluster_arn, services=service_arns).get("services") or []
        for svc in services:
            desired, running = svc.get("desiredCount", 0), svc.get("runningCount", 0)
            out.append(
                {
                    "type": "ecs",
                    "id": f"{cluster_name}/{svc['serviceName']}",
                    "name": svc["serviceName"],
                    "detail": f"cluster {cluster_name} · {svc.get('launchType', 'n/a')} · {running}/{desired} tasks",
                    "status": svc.get("status"),
                    "flags": (["desired-running-mismatch"] if desired != running else []),
                    "severity": ("medium" if desired != running else None),
                }
            )
    return out


async def list_ecs_services(*, region=None):
    """ECS clusters/services — desired vs running task counts. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_ecs_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check ecs:ListClusters/ListServices/DescribeServices permission"}


# ---------- EKS ----------

def _list_eks_sync(region):
    client = _client("eks", region)
    out = []
    for name in client.list_clusters().get("clusters") or []:
        detail = client.describe_cluster(name=name)["cluster"]
        out.append(
            {
                "type": "eks",
                "id": name,
                "name": name,
                "detail": f"k8s {detail.get('version')} · endpoint {'public' if (detail.get('resourcesVpcConfig') or {}).get('endpointPublicAccess') else 'private'}",
                "status": detail.get("status"),
                "flags": (["public-api-endpoint"] if (detail.get("resourcesVpcConfig") or {}).get("endpointPublicAccess") else []),
                "severity": ("high" if (detail.get("resourcesVpcConfig") or {}).get("endpointPublicAccess") else None),
            }
        )
    return out


async def list_eks_clusters(*, region=None):
    """EKS clusters — version, endpoint exposure. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_eks_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check eks:ListClusters/DescribeCluster permission"}


# ---------- ECR (incl. image vulnerability scan findings) ----------

def _list_ecr_sync(region):
    client = _client("ecr", region)
    out = []
    repos = client.describe_repositories().get("repositories") or []
    for repo in repos:
        repo_name = repo["repositoryName"]
        sev_counts = {}
        latest_tag = None
        try:
            images = client.describe_images(repositoryName=repo_name, maxResults=5).get("imageDetails") or []
            images.sort(key=lambda i: i.get("imagePushedAt") or 0, reverse=True)
            if images:
                latest = images[0]
                latest_tag = (latest.get("imageTags") or ["untagged"])[0]
                summary = (latest.get("imageScanFindingsSummary") or {}).get("findingSeverityCounts") or {}
                sev_counts = summary
                if not sev_counts:
                    # scan summary not embedded — ask ECR directly for this image's findings
                    findings = client.describe_image_scan_findings(
                        repositoryName=repo_name,
                        imageId={"imageTag": latest_tag} if latest_tag != "untagged" else {"imageDigest": latest["imageDigest"]},
                    )
                    sev_counts = (findings.get("imageScanFindings") or {}).get("findingSeverityCounts") or {}
        except Exception:  # noqa: BLE001
            pass  # scanning may be disabled on this repo, or no images yet — leave sev_counts empty

        critical_high = sev_counts.get("CRITICAL", 0) + sev_counts.get("HIGH", 0)
        any_vulns = sum(sev_counts.values()) if sev_counts else 0
        out.append(
            {
                "type": "ecr",
                "id": repo_name,
                "name": repo_name,
                "detail": f"latest image: {latest_tag or 'none'} · vulns: "
                + (", ".join(f"{sev_counts.get(s, 0)} {s.lower()}" for s in _SEV_ORDER if sev_counts.get(s)) or "none found"),
                "status": "vulnerable" if critical_high else "ok",
                "vulnSeverityCounts": sev_counts,
                "flags": (["critical-or-high-vulnerabilities"] if critical_high else []),
                "severity": "high" if critical_high else ("low" if any_vulns else None),
            }
        )
    return out


async def list_ecr_repositories(*, region=None):
    """ECR repos + latest image's vulnerability scan findings (severity counts). Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_ecr_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check ecr:DescribeRepositories/DescribeImages/DescribeImageScanFindings permission"}


# ---------- S3 ----------

def _list_s3_sync():
    client = boto3.client("s3", region_name=REGION)
    out = []
    buckets = (client.list_buckets().get("Buckets") or [])[:40]  # cap — accounts can have hundreds
    for b in buckets:
        name = b["Name"]
        public = "unknown"
        try:
            pab = client.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
            blocked_all = all(pab.values())
            public = "blocked" if blocked_all else "partially-open"
        except Exception:  # noqa: BLE001
            public = "no-block-configured"
        encrypted = True
        try:
            client.get_bucket_encryption(Bucket=name)
        except Exception:  # noqa: BLE001
            encrypted = False
        flags = []
        if public != "blocked":
            flags.append("public-access-not-fully-blocked")
        if not encrypted:
            flags.append("no-default-encryption")
        severity = "high" if public != "blocked" else ("medium" if not encrypted else None)
        out.append(
            {
                "type": "s3",
                "id": name,
                "name": name,
                "detail": f"public access: {public} · default encryption: {'on' if encrypted else 'off'}",
                "status": "flagged" if flags else "ok",
                "flags": flags,
                "severity": severity,
            }
        )
    return out


async def list_s3_buckets():
    """S3 buckets (global, capped at 40) — public-access-block + encryption status. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_s3_sync)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check s3:ListAllMyBuckets/GetPublicAccessBlock/GetEncryptionConfiguration permission"}


# ---------- Load balancers (ALB/NLB/GWLB via elbv2, plus legacy Classic LB via elb) ----------

def _list_elb_sync(region):
    out = []

    # ALB / NLB / Gateway LB — describe_load_balancers with no Names filter already
    # returns every one regardless of scheme (internet-facing AND internal).
    v2 = _client("elbv2", region)
    for lb in v2.describe_load_balancers().get("LoadBalancers") or []:
        out.append(
            {
                "type": "elb",
                "id": lb["LoadBalancerName"],
                "name": lb["LoadBalancerName"],
                "detail": f"{lb.get('Type')} · {lb.get('Scheme')}",
                "status": lb.get("State", {}).get("Code"),
                "flags": (["internet-facing"] if lb.get("Scheme") == "internet-facing" else []),
                "severity": ("low" if lb.get("Scheme") == "internet-facing" else None),
            }
        )

    # Legacy Classic Load Balancers live under the separate v1 'elb' API — easy to miss
    # since they don't show up in describe_load_balancers on elbv2 at all.
    classic = _client("elb", region)
    for lb in classic.describe_load_balancers().get("LoadBalancerDescriptions") or []:
        scheme = lb.get("Scheme", "internet-facing")
        out.append(
            {
                "type": "elb",
                "id": lb["LoadBalancerName"],
                "name": lb["LoadBalancerName"],
                "detail": f"classic · {scheme}",
                "status": "active",
                "flags": (["internet-facing"] if scheme == "internet-facing" else []) + ["classic-load-balancer-consider-migrating"],
                "severity": "low",
            }
        )

    return out


async def list_load_balancers(*, region=None):
    """Every load balancer — ALB/NLB/GWLB (elbv2) + legacy Classic LB (elb v1) — scheme, state. Feeds resource inventory."""
    try:
        return await asyncio.to_thread(_list_elb_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check elasticloadbalancing:DescribeLoadBalancers permission"}


async def get_full_inventory(*, region=None):
    """
    Every resource type this console can see, in one normalised list, plus the
    per-category raw results. Each item: {type, id, name, detail, status, flags}.
    Errors per category are isolated — one disabled service doesn't blank the rest.
    """
    (
        ec2, ebs, eip, nat, sg, dynamodb, elasticache, cloudfront, sns, sqs,
        rds, lambdas, ecs, eks, ecr, s3, elb,
    ) = await asyncio.gather(
        list_ec2_instances_for_inventory(region=region),
        list_ebs_volumes(region=region),
        list_elastic_ips(region=region),
        list_nat_gateways(region=region),
        list_security_groups(region=region),
        list_dynamodb_tables(region=region),
        list_elasticache_clusters(region=region),
        list_cloudfront_distributions(),
        list_sns_topics(region=region),
        list_sqs_queues(region=region),
        list_rds_instances(region=region),
        list_lambda_functions(region=region),
        list_ecs_services(region=region),
        list_eks_clusters(region=region),
        list_ecr_repositories(region=region),
        list_s3_buckets(),
        list_load_balancers(region=region),
    )
    by_category = {
        "ec2": ec2, "ebs": ebs, "eip": eip, "nat": nat, "sg": sg, "dynamodb": dynamodb,
        "elasticache": elasticache, "cloudfront": cloudfront, "sns": sns, "sqs": sqs,
        "rds": rds, "lambda": lambdas, "ecs": ecs, "eks": eks, "ecr": ecr, "s3": s3, "elb": elb,
    }

    resources = []
    errors = {}
    for category, items in by_category.items():
        if isinstance(items, dict) and items.get("error"):
            errors[category] = items
            continue
        resources.extend(items)

    flagged = [r for r in resources if r.get("flags")]
    return {
        "resources": resources,
        "byCategory": by_category,
        "errors": errors,
        "counts": {"total": len(resources), "flagged": len(flagged)},
        "flagged": flagged,
    }
