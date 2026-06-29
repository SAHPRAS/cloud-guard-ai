import asyncio
import os

import boto3

REGION = os.environ.get("AWS_REGION", "eu-central-1")
_ec2 = boto3.client("ec2", region_name=REGION)


def _list_ec2_instances_sync(region):
    client = _ec2 if not region or region == REGION else boto3.client("ec2", region_name=region.split(" ")[0])
    res = client.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    )
    instances = []
    for reservation in res.get("Reservations") or []:
        for inst in reservation.get("Instances") or []:
            name = next(
                (t["Value"] for t in inst.get("Tags") or [] if t["Key"] == "Name"), None
            )
            instances.append(
                {
                    "instance_id": inst["InstanceId"],
                    "type": inst["InstanceType"],
                    "state": inst["State"]["Name"],
                    "name": name,
                }
            )
    return instances


async def list_ec2_instances(*, region=None):
    """Running EC2 instances — id, type, state, name tag. Feeds the Rightsizing agent."""
    try:
        return await asyncio.to_thread(_list_ec2_instances_sync, region)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check ec2:DescribeInstances permission for this region"}
