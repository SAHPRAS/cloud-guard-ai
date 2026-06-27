import asyncio
import os
import re

import boto3

REGION = os.environ.get("AWS_REGION", "eu-central-1")
_sts = boto3.client("sts", region_name=REGION)


def _get_caller_identity_sync():
    res = _sts.get_caller_identity()
    # Assumed-role ARN looks like:
    # arn:aws:sts::211125387793:assumed-role/UnITe-Admin/session
    arn = res.get("Arn") or ""
    role_match = re.search(r"assumed-role/([^/]+)", arn)
    return {
        "account": res.get("Account"),
        "role": role_match.group(1) if role_match else "instance-role",
        "arn": arn,
        "profile": os.environ.get("PROFILE_LABEL", "DT_DTRD_DEV"),
        "region": REGION,
    }


async def get_caller_identity():
    """Who am I — powers the profile/account/role bar in the UI."""
    try:
        return await asyncio.to_thread(_get_caller_identity_sync)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "profile": os.environ.get("PROFILE_LABEL", "unknown")}