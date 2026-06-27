import asyncio
import os

import boto3

REGION = os.environ.get("AWS_REGION", "eu-central-1")
_sh = boto3.client("securityhub", region_name=REGION)
_gd = boto3.client("guardduty", region_name=REGION)

_SEV_MAP = {
    "CRITICAL": "crit",
    "HIGH": "crit",
    "MEDIUM": "warn",
    "LOW": "ok",
    "INFORMATIONAL": "ok",
}


def _get_security_hub_findings_sync(max_results):
    res = _sh.get_findings(
        Filters={
            "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
            "WorkflowStatus": [{"Value": "NEW", "Comparison": "EQUALS"}],
        },
        MaxResults=max_results,
    )
    findings = res.get("Findings") or []
    return [
        {
            "title": f.get("Title"),
            "desc": (f.get("Description") or "")[:160],
            "severity": _SEV_MAP.get((f.get("Severity") or {}).get("Label"), "warn"),
            "resource": (f.get("Resources") or [{}])[0].get("Id"),
            "type": (f.get("Types") or [None])[0],
        }
        for f in findings
    ]


async def get_security_hub_findings(*, max=25):
    """Active SecurityHub findings, normalised for the UI."""
    try:
        return await asyncio.to_thread(_get_security_hub_findings_sync, max)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Enable SecurityHub in this region"}


def _get_guard_duty_findings_sync(max_results):
    detectors = _gd.list_detectors()
    detector_ids = detectors.get("DetectorIds") or []
    if not detector_ids:
        return []
    detector_id = detector_ids[0]

    listed = _gd.list_findings(DetectorId=detector_id, MaxResults=max_results)
    finding_ids = listed.get("FindingIds") or []
    if not finding_ids:
        return []

    detailed = _gd.get_findings(DetectorId=detector_id, FindingIds=finding_ids)
    return [
        {
            "title": f.get("Type"),
            "desc": (f.get("Description") or "")[:160],
            "severity": "crit" if f.get("Severity", 0) >= 7 else ("warn" if f.get("Severity", 0) >= 4 else "ok"),
            "resource": (f.get("Resource") or {}).get("ResourceType"),
        }
        for f in detailed.get("Findings") or []
    ]


async def get_guard_duty_findings(*, max=25):
    """Active GuardDuty findings."""
    try:
        return await asyncio.to_thread(_get_guard_duty_findings_sync, max)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "hint": "Enable GuardDuty in this region"}
