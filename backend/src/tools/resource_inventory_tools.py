import asyncio
import base64
import os
import re
import tempfile
import contextlib
import urllib.request
from datetime import datetime

import boto3
from botocore.signers import RequestSigner
from kubernetes import client as k8s_client
from bson import ObjectId
from bson.decimal128 import Decimal128
from pymongo import MongoClient

REGION = os.environ.get("AWS_REGION", "eu-central-1")

_SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL", "UNDEFINED"]

# EKS cluster -> application namespace this console knows how to drill into for
# node/pod visibility. Add an entry here to surface a new cluster's workloads.
EKS_NAMESPACES = {
    "doc-dev-eks-cluster": "doc-dev",
    "rds-rms-dev-eks-cluster": "rds-rms-dev",
}
# Matched case-insensitively — EKS cluster names come back from AWS in whatever case
# they were created with, which doesn't always match how people write them elsewhere.
_EKS_NAMESPACES_LOWER = {name.lower(): namespace for name, namespace in EKS_NAMESPACES.items()}


def _namespace_for_cluster(cluster_name):
    return _EKS_NAMESPACES_LOWER.get(cluster_name.lower())


# RDS/DocumentDB instance -> the single collection this console knows how to sample
# documents from. Matched case-insensitively, same reasoning as EKS_NAMESPACES above.
DOCDB_COLLECTIONS = {
    "rds-rms-dev-db-cluster-instance-1": {"database": "consentdb", "collection": "consent"},
}
_DOCDB_COLLECTIONS_LOWER = {name.lower(): target for name, target in DOCDB_COLLECTIONS.items()}


def _docdb_target_for_instance(instance_id):
    return _DOCDB_COLLECTIONS_LOWER.get(instance_id.lower())


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


# ---------- DocumentDB collection sample (for the RDS instances in DOCDB_COLLECTIONS) ----------
# DocumentDB has no IAM-token auth like RDS/Aurora — login is username/password only, read
# from DOCDB_URI (never hardcoded; set it in an untracked .env, see .env.example).

_DOCDB_CA_BUNDLE_URL = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
_DOCDB_CA_BUNDLE_PATH = os.path.join(tempfile.gettempdir(), "rds-global-bundle.pem")
_DOCDB_TIMEOUT_MS = 8000
_DOCDB_SAMPLE_LIMIT = 20


def _docdb_ca_bundle():
    # AWS's public CA bundle for RDS/DocumentDB TLS — same file for every cluster/region,
    # so it's downloaded once and reused for the life of the process.
    if not os.path.exists(_DOCDB_CA_BUNDLE_PATH):
        urllib.request.urlretrieve(_DOCDB_CA_BUNDLE_URL, _DOCDB_CA_BUNDLE_PATH)
    return _DOCDB_CA_BUNDLE_PATH


def _jsonify_bson(value):
    # Mongo documents carry BSON types (ObjectId, datetime, Decimal128) that aren't
    # natively JSON-serializable — convert them to plain strings recursively.
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal128):
        return str(value.to_decimal())
    if isinstance(value, dict):
        return {k: _jsonify_bson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify_bson(v) for v in value]
    return value


def _fetch_doc_collection_sync(database, collection):
    uri = os.environ.get("DOCDB_URI")
    if not uri:
        return {
            "database": database,
            "collection": collection,
            "error": "DOCDB_URI is not set",
            "hint": "Set DOCDB_URI in this backend's .env (never commit the real value) — see .env.example",
        }

    client = None
    try:
        client = MongoClient(
            uri,
            tls=True,
            tlsCAFile=_docdb_ca_bundle(),
            retryWrites=False,
            serverSelectionTimeoutMS=_DOCDB_TIMEOUT_MS,
            connectTimeoutMS=_DOCDB_TIMEOUT_MS,
        )
        coll = client[database][collection]
        count = coll.estimated_document_count()
        documents = [_jsonify_bson(doc) for doc in coll.find()]
        return {"database": database, "collection": collection, "count": count, "documents": documents}
    except Exception as e:  # noqa: BLE001
        return {
            "database": database,
            "collection": collection,
            "error": str(e),
            "hint": f"Check DOCDB_URI's credentials, and that this backend has network access "
            f"(VPC route / security group) to reach {database}.{collection}",
        }
    finally:
        if client is not None:
            client.close()


async def list_docdb_collections(instances):
    """Document sample (capped at 20) for whichever RDS instances match DOCDB_COLLECTIONS."""
    pairs = [(name, _docdb_target_for_instance(name)) for name in instances]
    pairs = [(name, target) for name, target in pairs if target]
    results = await asyncio.gather(
        *(asyncio.to_thread(_fetch_doc_collection_sync, target["database"], target["collection"]) for _, target in pairs)
    )
    return {name: result for (name, _), result in zip(pairs, results)}


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


# ---------- EKS node/pod workloads (nodes + application-namespace pods) ----------
# Kubernetes API access is a separate permission boundary from AWS IAM: the caller's
# AWS principal must also be mapped into the cluster's aws-auth ConfigMap (or have an
# EKS access entry) with at least view access, or every call below fails with 401/403.

def _eks_bearer_token(cluster_name, region_code):
    # Same token scheme `aws eks get-token` / aws-iam-authenticator use: a presigned
    # STS GetCallerIdentity URL, base64-encoded, with the cluster name embedded so the
    # API server can bind the token to this specific cluster.
    session = boto3.session.Session()
    sts = session.client("sts", region_name=region_code)
    signer = RequestSigner(
        sts.meta.service_model.service_id, region_code, "sts", "v4", session.get_credentials(), session.events
    )
    url = signer.generate_presigned_url(
        {
            "method": "GET",
            "url": f"https://sts.{region_code}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
            "body": {},
            "headers": {"x-k8s-aws-id": cluster_name},
            "context": {},
        },
        region_name=region_code,
        expires_in=60,
        operation_name="",
    )
    token = base64.urlsafe_b64encode(url.encode("utf-8")).decode("utf-8")
    return "k8s-aws-v1." + re.sub(r"=*$", "", token)


def _k8s_api_for_cluster(cluster_name, region):
    region_code = _region_code(region)
    eks = _client("eks", region)
    detail = eks.describe_cluster(name=cluster_name)["cluster"]

    cafile = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
    cafile.write(base64.b64decode(detail["certificateAuthority"]["data"]))
    cafile.close()

    config = k8s_client.Configuration()
    config.host = detail["endpoint"]
    config.ssl_ca_cert = cafile.name
    config.api_key = {"authorization": _eks_bearer_token(cluster_name, region_code)}
    config.api_key_prefix = {"authorization": "Bearer"}

    return k8s_client.CoreV1Api(k8s_client.ApiClient(config))


# (connect, read) seconds — without this the kubernetes client blocks indefinitely on an
# unreachable cluster (e.g. a private endpoint this backend has no network path to),
# which stalls the whole scan past nginx's proxy_read_timeout and surfaces as an HTML
# 504 page instead of JSON on the frontend.
_K8S_TIMEOUT = (5, 10)


def _list_eks_workloads_sync(cluster_name, namespace, region):
    try:
        v1 = _k8s_api_for_cluster(cluster_name, region)

        nodes = []
        for n in v1.list_node(_request_timeout=_K8S_TIMEOUT).items:
            labels = n.metadata.labels or {}
            conditions = {c.type: c.status for c in (n.status.conditions or [])}
            nodes.append(
                {
                    "name": n.metadata.name,
                    "status": "Ready" if conditions.get("Ready") == "True" else "NotReady",
                    "instanceType": labels.get("node.kubernetes.io/instance-type", "—"),
                    "az": labels.get("topology.kubernetes.io/zone", "—"),
                }
            )

        pods = []
        for p in v1.list_namespaced_pod(namespace, _request_timeout=_K8S_TIMEOUT).items:
            statuses = p.status.container_statuses or []
            ready = sum(1 for s in statuses if s.ready)
            pods.append(
                {
                    "name": p.metadata.name,
                    "status": p.status.phase,
                    "node": p.spec.node_name,
                    "ready": f"{ready}/{len(statuses)}",
                    "restarts": sum(s.restart_count for s in statuses),
                }
            )

        return {"namespace": namespace, "nodes": nodes, "pods": pods}
    except Exception as e:  # noqa: BLE001
        return {
            "namespace": namespace,
            "error": str(e),
            "hint": f"Check that this backend's AWS principal has a Kubernetes access entry (or aws-auth ConfigMap "
            f"entry) with view access on {cluster_name}",
        }


async def list_eks_workloads(cluster_names, *, region=None):
    """Nodes + application-namespace pods for the given (real, case-preserved) EKS cluster
    names that match EKS_NAMESPACES case-insensitively."""
    pairs = [(name, _namespace_for_cluster(name)) for name in cluster_names]
    pairs = [(name, namespace) for name, namespace in pairs if namespace]
    results = await asyncio.gather(
        *(asyncio.to_thread(_list_eks_workloads_sync, name, namespace, region) for name, namespace in pairs)
    )
    return {name: result for (name, _), result in zip(pairs, results)}


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

    # Attach node/pod workloads to whichever known clusters actually showed up in this
    # account/region — a fresh Kubernetes API call per cluster, so skip it entirely
    # when there's nothing in EKS_NAMESPACES to look up.
    if isinstance(eks, list):
        matched_names = [c["name"] for c in eks if _namespace_for_cluster(c["name"])]
        if matched_names:
            workloads = await list_eks_workloads(matched_names, region=region)
            for cluster in eks:
                if cluster["name"] in workloads:
                    cluster["workloads"] = workloads[cluster["name"]]

    # Same idea for RDS instances in DOCDB_COLLECTIONS — a capped document sample
    # pulled straight from the DocumentDB collection, not just AWS-side metadata.
    if isinstance(rds, list):
        matched_instances = [db["name"] for db in rds if _docdb_target_for_instance(db["name"])]
        if matched_instances:
            collections = await list_docdb_collections(matched_instances)
            for db in rds:
                if db["name"] in collections:
                    db["docCollection"] = collections[db["name"]]

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
