import asyncio
import os
from datetime import datetime, timedelta

import boto3

REGION = os.environ.get("AWS_REGION", "eu-central-1")
_cw = boto3.client("cloudwatch", region_name=REGION)


def _get_ec2_cpu_utilization_sync(instance_id, region, days):
    client = _cw if not region or region == REGION else boto3.client("cloudwatch", region_name=region.split(" ")[0])
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    res = client.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start,
        EndTime=end,
        Period=3600,
        Statistics=["Average", "Maximum"],
    )
    points = res.get("Datapoints") or []
    if not points:
        return {"instance_id": instance_id, "avg": None, "max": None, "samples": 0}
    avg = sum(p["Average"] for p in points) / len(points)
    peak = max(p["Maximum"] for p in points)
    return {"instance_id": instance_id, "avg": round(avg, 1), "max": round(peak, 1), "samples": len(points)}


async def get_ec2_cpu_utilization(*, instance_id, region=None, days=14):
    """Average/max CPU% for an EC2 instance over the trailing N days. Grounds rightsizing calls in real usage."""
    try:
        return await asyncio.to_thread(_get_ec2_cpu_utilization_sync, instance_id, region, days)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Check cloudwatch:GetMetricStatistics permission for this region"}
