import asyncio
import os
from datetime import date, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()  # must run before the agent/tool imports below read os.environ at module load

from .agents.anomaly_detector import run_anomaly_detector
from .agents.cost_analyst import run_cost_analyst
from .agents.forecasting import run_forecasting
from .agents.orchestrator import classify_intent
from .agents.rightsizing import run_rightsizing
from .agents.security import run_security
from .tools.athena_cur_tools import get_cur_cost_by_service
from .tools.cost_explorer_tools import get_cost_by_service, month_to_range
from .tools.sts_tools import get_caller_identity

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PORT = int(os.environ.get("PORT", 3001))


def is_future_month(month):
    """Is the requested month in the future? -> forecast mode."""
    start = month_to_range(month)["Start"]
    now = date.today().replace(day=1)
    return date.fromisoformat(start) > now


def is_past_month(month):
    """Has this month already fully ended? Forecasting a closed month is meaningless."""
    start = month_to_range(month)["Start"]
    now = date.today().replace(day=1)
    return date.fromisoformat(start) < now


def current_month_label():
    return date.today().strftime("%Y-%m")


def previous_month_label(month):
    start = date.fromisoformat(month_to_range(month)["Start"])
    return (start - timedelta(days=1)).strftime("%Y-%m")


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/cur-cost")
async def cur_cost(month: str = "JUN 26"):
    """
    Exact per-service cost straight from the CUR via Athena — for verifying
    it reconciles with the Bills page before we wire it in as the main
    data source for cost_analyst/anomaly/forecasting.
    e.g. /api/cur-cost?month=2026-05
    """
    try:
        return JSONResponse(await get_cur_cost_by_service(month=month))
    except Exception as err:  # noqa: BLE001
        return JSONResponse({"error": str(err)}, status_code=500)


@app.get("/api/identity")
async def identity():
    """Profile / account / role bar."""
    return await get_caller_identity()


@app.post("/api/scan")
async def scan(request: Request):
    """
    Full or individual scan.
    body: { target: 'full'|'cost'|'anomaly'|'rightsizing'|'forecast'|'security', month, region }
    """
    body = await request.json() if await request.body() else {}
    target = body.get("target", "full")
    month = body.get("month", current_month_label())
    region = body.get("region", "eu-central-1")
    future = is_future_month(month)

    try:
        result = {"target": target, "month": month, "region": region, "mode": "forecast" if future else "live", "blocks": {}}

        # future months => projection only
        if future:
            result["blocks"]["forecast"] = await run_forecasting(month=month, region=region)
            return JSONResponse(result)

        past = is_past_month(month)
        if target == "forecast" and past:
            result["blocks"]["forecast"] = {"error": f"{month} has already ended — forecasts are only available for the current or future months."}
            return JSONResponse(result)

        def wants(t):
            return target == "full" or target == t

        tasks = []
        if wants("cost"):
            tasks.append(("cost", run_cost_analyst(month=month, region=region)))
        if wants("anomaly"):
            tasks.append(("anomaly", run_anomaly_detector(region=region)))
        if wants("rightsizing"):
            tasks.append(("rightsizing", run_rightsizing(month=month, region=region)))
        if wants("forecast") and not past:
            tasks.append(("forecast", run_forecasting(month=month, region=region)))
        if wants("security"):
            tasks.append(("security", run_security()))

        settled = await asyncio.gather(*(p for _, p in tasks), return_exceptions=True)
        for (key, _), value in zip(tasks, settled):
            result["blocks"][key] = {"error": str(value)} if isinstance(value, Exception) else value

        # current (in-progress) month -> compare month-to-date spend against the previous month
        cost_block = result["blocks"].get("cost")
        if not past and isinstance(cost_block, dict) and "data" in cost_block:
            try:
                prev_month = previous_month_label(month)
                prev_data = await get_cost_by_service(month=prev_month, region=region)
                current_total = cost_block["data"]["total"]
                previous_total = prev_data["total"]
                delta = round(current_total - previous_total, 2)
                cost_block["comparison"] = {
                    "previousMonth": prev_month,
                    "previousTotal": previous_total,
                    "currentTotal": current_total,
                    "delta": delta,
                    "deltaPct": round(delta / previous_total * 100, 1) if previous_total else None,
                }
            except Exception:  # noqa: BLE001
                pass  # comparison is a nice-to-have; don't fail the whole scan over it

        return JSONResponse(result)
    except Exception as err:  # noqa: BLE001
        return JSONResponse({"error": str(err)}, status_code=500)


@app.post("/api/query")
async def query(request: Request):
    """
    Chat: orchestrator routes to the right agent.
    body: { query }
    """
    body = await request.json() if await request.body() else {}
    user_query = body.get("query")
    month = body.get("month", current_month_label())
    region = body.get("region", "eu-central-1")

    try:
        intent = await classify_intent(user_query)
        if intent == "anomaly":
            result = await run_anomaly_detector(region=region)
        elif intent == "rightsizing":
            result = await run_rightsizing(month=month, region=region)
        elif intent == "forecast":
            if is_past_month(month):
                result = {"summary": f"{month} has already ended — forecasts are only available for the current or future months."}
            else:
                result = await run_forecasting(month=month, region=region)
        elif intent == "security":
            result = await run_security()
        else:
            result = await run_cost_analyst(month=month, region=region)

        return JSONResponse({"intent": intent, **result})
    except Exception as err:  # noqa: BLE001
        return JSONResponse({"error": str(err)}, status_code=500)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
