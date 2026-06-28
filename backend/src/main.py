import asyncio
import os
from datetime import date

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
from .tools.cost_explorer_tools import get_cost_breakdown, get_cost_diagnostics, month_to_range
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


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/identity")
async def identity():
    """Profile / account / role bar."""
    return await get_caller_identity()


@app.get("/api/cost-breakdown")
async def cost_breakdown(month: str = "JUN 26", region: str = None):
    """Per-service cost split into Usage/Discount/Credit/Tax/RIFee components — for the expandable rows in the cost table."""
    try:
        return JSONResponse(await get_cost_breakdown(month=month, region=region))
    except Exception as err:  # noqa: BLE001
        return JSONResponse({"error": str(err)}, status_code=500)


@app.get("/api/diagnose-cost")
async def diagnose_cost(month: str = "JUN 26"):
    """
    Compare every Cost Explorer metric (Unblended/Net.../Amortized) plus a
    RECORD_TYPE breakdown for a month, to reconcile against the Bills page
    grand total. e.g. /api/diagnose-cost?month=2026-05
    """
    try:
        return JSONResponse(await get_cost_diagnostics(month=month))
    except Exception as err:  # noqa: BLE001
        return JSONResponse({"error": str(err)}, status_code=500)


@app.post("/api/scan")
async def scan(request: Request):
    """
    Full or individual scan.
    body: { target: 'full'|'cost'|'anomaly'|'rightsizing'|'forecast'|'security', month, region }
    """
    body = await request.json() if await request.body() else {}
    target = body.get("target", "full")
    month = body.get("month", "JUN 26")
    region = body.get("region", "eu-central-1")
    future = is_future_month(month)

    try:
        result = {"target": target, "month": month, "region": region, "mode": "forecast" if future else "live", "blocks": {}}

        # future months => projection only
        if future:
            result["blocks"]["forecast"] = await run_forecasting(month=month, region=region)
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
        if wants("forecast"):
            tasks.append(("forecast", run_forecasting(month=month, region=region)))
        if wants("security"):
            tasks.append(("security", run_security()))

        settled = await asyncio.gather(*(p for _, p in tasks), return_exceptions=True)
        for (key, _), value in zip(tasks, settled):
            result["blocks"][key] = {"error": str(value)} if isinstance(value, Exception) else value

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
    month = body.get("month", "JUN 26")
    region = body.get("region", "eu-central-1")

    try:
        intent = await classify_intent(user_query)
        if intent == "anomaly":
            result = await run_anomaly_detector(region=region)
        elif intent == "rightsizing":
            result = await run_rightsizing(month=month, region=region)
        elif intent == "forecast":
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
