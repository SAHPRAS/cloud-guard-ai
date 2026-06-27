import "dotenv/config";
import express from "express";
import cors from "cors";

import { getCallerIdentity } from "./tools/stsTools.js";
import { classifyIntent } from "./agents/orchestrator.js";
import { runCostAnalyst } from "./agents/costAnalyst.js";
import { runAnomalyDetector } from "./agents/anomalyDetector.js";
import { runRightsizing } from "./agents/rightsizing.js";
import { runForecasting } from "./agents/forecasting.js";
import { runSecurity } from "./agents/security.js";
import { monthToRange } from "./tools/costExplorerTools.js";

const app = express();
app.use(cors());
app.use(express.json());

const PORT = process.env.PORT || 3001;

// Is the requested month in the future? -> forecast mode
function isFutureMonth(month) {
  const start = monthToRange(month).Start;
  const now = new Date();
  now.setDate(1);
  return new Date(start) > now;
}

app.get("/api/health", (_req, res) => res.json({ ok: true }));

// Profile / account / role bar
app.get("/api/identity", async (_req, res) => {
  res.json(await getCallerIdentity());
});

// Full or individual scan.
// body: { target: 'full'|'cost'|'anomaly'|'rightsizing'|'forecast'|'security', month, region }
app.post("/api/scan", async (req, res) => {
  const { target = "full", month = "JUN 26", region = "eu-central-1" } = req.body || {};
  const future = isFutureMonth(month);

  try {
    const result = { target, month, region, mode: future ? "forecast" : "live", blocks: {} };

    // future months => projection only
    if (future) {
      result.blocks.forecast = await runForecasting({ month, region });
      return res.json(result);
    }

    const wants = (t) => target === "full" || target === t;

    const tasks = [];
    if (wants("cost")) tasks.push(["cost", runCostAnalyst({ month, region })]);
    if (wants("anomaly")) tasks.push(["anomaly", runAnomalyDetector({ region })]);
    if (wants("rightsizing")) tasks.push(["rightsizing", runRightsizing({ month, region })]);
    if (wants("forecast")) tasks.push(["forecast", runForecasting({ month, region })]);
    if (wants("security")) tasks.push(["security", runSecurity()]);

    const settled = await Promise.allSettled(tasks.map(([, p]) => p));
    settled.forEach((s, i) => {
      const key = tasks[i][0];
      result.blocks[key] = s.status === "fulfilled" ? s.value : { error: String(s.reason) };
    });

    res.json(result);
  } catch (err) {
    res.status(500).json({ error: String(err.message) });
  }
});

// Chat: orchestrator routes to the right agent.
// body: { query }
app.post("/api/query", async (req, res) => {
  const { query, month = "JUN 26", region = "eu-central-1" } = req.body || {};
  try {
    const intent = await classifyIntent(query);
    let result;
    switch (intent) {
      case "anomaly": result = await runAnomalyDetector({ region }); break;
      case "rightsizing": result = await runRightsizing({ month, region }); break;
      case "forecast": result = await runForecasting({ month, region }); break;
      case "security": result = await runSecurity(); break;
      default: result = await runCostAnalyst({ month, region });
    }
    res.json({ intent, ...result });
  } catch (err) {
    res.status(500).json({ error: String(err.message) });
  }
});

app.listen(PORT, () => console.log(`Cloud Guard AI backend on :${PORT}`));
