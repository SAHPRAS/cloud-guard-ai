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


async def get_cur_cost_by_service(*, month):
    """
    Per-service cost for a month, summed from line_item_net_unblended_cost
    (nets out credits/discounts). Note: this account's table has no
    region/RI/Savings-Plan columns, so this is account-wide and assumes no
    RI/Savings Plan commitments — if those exist, this won't include
    amortization and may not match NetAmortizedCost-based totals.
    """
    period = month_to_range(month)
    sql = f"""
        SELECT
            line_item_product_code AS service,
            SUM(line_item_net_unblended_cost) AS amount
        FROM {DATABASE}.{TABLE}
        WHERE CAST(bill_billing_period_start_date AS date) >= DATE '{period["Start"]}'
          AND CAST(bill_billing_period_start_date AS date) < DATE '{period["End"]}'
        GROUP BY line_item_product_code
        HAVING SUM(line_item_net_unblended_cost) > 0
        ORDER BY amount DESC
    """
    rows = await run_athena_query(sql)
    services = [{"service": r["service"], "amount": round(float(r["amount"]), 2)} for r in rows]
    total = round(sum(s["amount"] for s in services), 2)
    return {"period": period, "total": total, "services": services}
