"""
Exact-to-the-cent cost data straight from the Cost & Usage Report via Athena.
This is the literal line-item data AWS bills you from, so totals reconcile
exactly with the Bills page — unlike Cost Explorer's aggregated metrics,
which can drift for EDP/private-rate discounts on linked accounts.
"""
import asyncio
import os
import time

import boto3

from .cost_explorer_tools import month_to_range

REGION = os.environ.get("ATHENA_REGION", "us-east-1")
DATABASE = os.environ.get("ATHENA_DATABASE", "cur_db")
TABLE = os.environ.get("ATHENA_TABLE", "aws_cur")
OUTPUT_S3 = os.environ.get("ATHENA_OUTPUT_S3", "s3://cloud-guard-ai/athena-results/")

_athena = boto3.client("athena", region_name=REGION)

_POLL_INTERVAL_S = 1
_MAX_POLLS = 60  # ~60s timeout


def _run_query_sync(sql):
    start = _athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        ResultConfiguration={"OutputLocation": OUTPUT_S3},
    )
    query_id = start["QueryExecutionId"]

    state = "RUNNING"
    for _ in range(_MAX_POLLS):
        exec_info = _athena.get_query_execution(QueryExecutionId=query_id)
        state = exec_info["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(_POLL_INTERVAL_S)
    else:
        raise TimeoutError(f"Athena query {query_id} did not finish within {_MAX_POLLS * _POLL_INTERVAL_S}s")

    if state != "SUCCEEDED":
        reason = exec_info["QueryExecution"]["Status"].get("StateChangeReason", "unknown error")
        raise RuntimeError(f"Athena query failed ({state}): {reason}")

    rows = []
    columns = None
    paginator = _athena.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=query_id):
        for row in page["ResultSet"]["Rows"]:
            values = [c.get("VarCharValue") for c in row["Data"]]
            if columns is None:
                columns = values  # header row
                continue
            rows.append(dict(zip(columns, values)))
    return rows


async def run_athena_query(sql):
    return await asyncio.to_thread(_run_query_sync, sql)


def _billing_period_value(month):
    """'JUN 26' / '2026-06' -> '2026-06' — matches the S3 billing_period=YYYY-MM partition."""
    return month_to_range(month)["Start"][:7]


async def get_cur_cost_by_service(*, month):
    """
    Per-service cost straight from the CUR for one billing period: usage_cost
    (gross/on-demand, line_item_unblended_cost), actual_cost (net of
    credits/discounts, line_item_net_unblended_cost), and discount (the
    difference) — plus a TOTAL row and the overall total_cost. Filtered on
    the billing_period Hive partition (e.g. billing_period=2026-06).
    """
    billing_period = _billing_period_value(month)
    sql = f"""
        SELECT
            line_item_product_code AS service,
            SUM(line_item_unblended_cost) AS usage_cost,
            SUM(line_item_net_unblended_cost) AS actual_cost
        FROM {DATABASE}.{TABLE}
        WHERE billing_period = '{billing_period}'
        GROUP BY line_item_product_code
        ORDER BY usage_cost DESC
    """
    rows = await run_athena_query(sql)

    services = []
    usage_total = 0.0
    actual_total = 0.0
    for r in rows:
        usage = round(float(r["usage_cost"]), 2)
        actual = round(float(r["actual_cost"]), 2)
        services.append({"service": r["service"], "usage_cost": usage, "actual_cost": actual, "discount": round(usage - actual, 2)})
        usage_total += usage
        actual_total += actual

    services.append({
        "service": "TOTAL",
        "usage_cost": round(usage_total, 2),
        "actual_cost": round(actual_total, 2),
        "discount": round(usage_total - actual_total, 2),
    })
    return {"billingPeriod": billing_period, "totalCost": round(actual_total, 2), "services": services}
