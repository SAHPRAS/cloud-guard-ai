import {
  CostExplorerClient,
  GetCostAndUsageCommand,
  GetCostForecastCommand,
} from "@aws-sdk/client-cost-explorer";

// Cost Explorer is a global service but the SDK requires a region.
const ce = new CostExplorerClient({ region: "us-east-1" });

// ---- simple in-memory cache (swap for Redis/ElastiCache later) ----
const cache = new Map();
const TTL_MS = 1000 * 60 * 60; // 1h
function cacheGet(key) {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.t < TTL_MS) return hit.v;
  return null;
}
function cacheSet(key, v) {
  cache.set(key, { t: Date.now(), v });
}

/** Convert "JUN 26" / "2026-06" into a Cost Explorer date window. */
export function monthToRange(month) {
  // accepts "2026-06" or "JUN 26"
  let year, mon;
  if (/^\d{4}-\d{2}$/.test(month)) {
    [year, mon] = month.split("-").map(Number);
  } else {
    const map = { JAN: 1, FEB: 2, MAR: 3, APR: 4, MAY: 5, JUN: 6, JUL: 7, AUG: 8, SEP: 9, OCT: 10, NOV: 11, DEC: 12 };
    const [m, y] = month.toUpperCase().split(" ");
    mon = map[m];
    year = 2000 + Number(y);
  }
  const start = `${year}-${String(mon).padStart(2, "0")}-01`;
  const next = mon === 12 ? `${year + 1}-01-01` : `${year}-${String(mon + 1).padStart(2, "0")}-01`;
  return { Start: start, End: next };
}

export async function getCostByService({ month, region }) {
  const key = `svc:${month}:${region}`;
  const cached = cacheGet(key);
  if (cached) return cached;

  const period = monthToRange(month);
  const filter = region && region !== "ALL REGIONS"
    ? { Dimensions: { Key: "REGION", Values: [region.split(" ")[0]] } }
    : undefined;

  const cmd = new GetCostAndUsageCommand({
    TimePeriod: period,
    Granularity: "MONTHLY",
    Metrics: ["UnblendedCost"],
    GroupBy: [{ Type: "DIMENSION", Key: "SERVICE" }],
    ...(filter ? { Filter: filter } : {}),
  });

  const res = await ce.send(cmd);
  const groups = res.ResultsByTime?.[0]?.Groups || [];
  const services = groups
    .map((g) => ({
      service: g.Keys[0],
      amount: Number(g.Metrics.UnblendedCost.Amount),
    }))
    .filter((s) => s.amount > 0)
    .sort((a, b) => b.amount - a.amount);

  const total = services.reduce((s, x) => s + x.amount, 0);
  const out = { period, total: Math.round(total), services };
  cacheSet(key, out);
  return out;
}

/** Trailing N months of monthly totals — feeds the forecast model. */
export async function getMonthlyTrend({ months = 12, region } = {}) {
  const end = new Date();
  end.setDate(1);
  const start = new Date(end);
  start.setMonth(start.getMonth() - months);
  const fmt = (d) => d.toISOString().slice(0, 10);

  const filter = region && region !== "ALL REGIONS"
    ? { Dimensions: { Key: "REGION", Values: [region.split(" ")[0]] } }
    : undefined;

  const cmd = new GetCostAndUsageCommand({
    TimePeriod: { Start: fmt(start), End: fmt(end) },
    Granularity: "MONTHLY",
    Metrics: ["UnblendedCost"],
    ...(filter ? { Filter: filter } : {}),
  });
  const res = await ce.send(cmd);
  return (res.ResultsByTime || []).map((r) => ({
    month: r.TimePeriod.Start.slice(0, 7),
    amount: Math.round(Number(r.Total.UnblendedCost.Amount)),
  }));
}

/** AWS-native ML forecast for a future window. */
export async function getCostForecast({ month, region }) {
  const period = monthToRange(month);
  const filter = region && region !== "ALL REGIONS"
    ? { Dimensions: { Key: "REGION", Values: [region.split(" ")[0]] } }
    : undefined;
  try {
    const cmd = new GetCostForecastCommand({
      TimePeriod: period,
      Granularity: "MONTHLY",
      Metric: "UNBLENDED_COST",
      PredictionIntervalLevel: 80,
      ...(filter ? { Filter: filter } : {}),
    });
    const res = await ce.send(cmd);
    const mean = Number(res.Total.Amount);
    const lo = Number(res.ForecastResultsByTime?.[0]?.PredictionIntervalLowerBound || mean * 0.88);
    const hi = Number(res.ForecastResultsByTime?.[0]?.PredictionIntervalUpperBound || mean * 1.12);
    return { projected: Math.round(mean), low: Math.round(lo), high: Math.round(hi), source: "ce:GetCostForecast" };
  } catch (e) {
    // CE forecast can fail without enough history — fall back to trend model.
    const trend = await getMonthlyTrend({ months: 12, region });
    return growthModelForecast(trend, month);
  }
}

/**
 * Trailing N months of cost grouped by service AND month.
 * Returns { months:[...], series: { [service]: [amt per month] } }.
 * This is what feeds the per-service forecast.
 */
export async function getServiceTrend({ months = 6, region } = {}) {
  const key = `svctrend:${months}:${region}`;
  const cached = cacheGet(key);
  if (cached) return cached;

  const end = new Date();
  end.setDate(1);
  const start = new Date(end);
  start.setMonth(start.getMonth() - months);
  const fmt = (d) => d.toISOString().slice(0, 10);

  const filter = region && region !== "ALL REGIONS"
    ? { Dimensions: { Key: "REGION", Values: [region.split(" ")[0]] } }
    : undefined;

  const cmd = new GetCostAndUsageCommand({
    TimePeriod: { Start: fmt(start), End: fmt(end) },
    Granularity: "MONTHLY",
    Metrics: ["UnblendedCost"],
    GroupBy: [{ Type: "DIMENSION", Key: "SERVICE" }],
    ...(filter ? { Filter: filter } : {}),
  });

  const res = await ce.send(cmd);
  const monthLabels = [];
  const series = {};
  (res.ResultsByTime || []).forEach((rt, i) => {
    monthLabels.push(rt.TimePeriod.Start.slice(0, 7));
    (rt.Groups || []).forEach((g) => {
      const svc = g.Keys[0];
      const amt = Number(g.Metrics.UnblendedCost.Amount);
      if (!series[svc]) series[svc] = new Array(res.ResultsByTime.length).fill(0);
      series[svc][i] = amt;
    });
  });

  const out = { months: monthLabels, series };
  cacheSet(key, out);
  return out;
}

/**
 * Forecast EACH service forward to a target future month, then sum.
 * Per-service compound growth from its own history; total = sum of services.
 * Returns { services:[{service, projected, low, high}], total, monthsAhead }.
 */
export async function forecastByService({ month, region }) {
  const { months: hist, series } = await getServiceTrend({ months: 6, region });
  if (!hist.length) return { services: [], total: 0, monthsAhead: 0 };

  // how many months ahead is the target?
  const target = monthToRange(month).Start.slice(0, 7);
  const [ty, tm] = target.split("-").map(Number);
  const [ly, lm] = hist[hist.length - 1].split("-").map(Number);
  const ahead = Math.max(1, (ty - ly) * 12 + (tm - lm));

  const services = Object.entries(series)
    .map(([service, arr]) => {
      const last = arr[arr.length - 1] || 0;
      // average month-over-month growth for THIS service
      let g = 0, n = 0;
      for (let i = 1; i < arr.length; i++) {
        if (arr[i - 1] > 0) { g += arr[i] / arr[i - 1] - 1; n++; }
      }
      const rate = n ? g / n : 0.04;
      const projected = last * Math.pow(1 + rate, ahead);
      return {
        service,
        projected: Math.round(projected),
        low: Math.round(projected * 0.88),
        high: Math.round(projected * 1.12),
        ratePct: Math.round(rate * 1000) / 10,
      };
    })
    .filter((s) => s.projected > 0)
    .sort((a, b) => b.projected - a.projected);

  const total = services.reduce((s, x) => s + x.projected, 0);
  return {
    services,
    total: Math.round(total),
    low: Math.round(total * 0.9),
    high: Math.round(total * 1.1),
    monthsAhead: ahead,
  };
}

/** Compound-growth fallback the Forecasting agent can also use to explain "why". */
export function growthModelForecast(trend, month) {
  if (!trend.length) return { projected: 0, low: 0, high: 0, source: "growth-model" };
  const last = trend[trend.length - 1].amount;
  // average month-over-month growth
  let growth = 0, n = 0;
  for (let i = 1; i < trend.length; i++) {
    if (trend[i - 1].amount > 0) { growth += trend[i].amount / trend[i - 1].amount - 1; n++; }
  }
  const rate = n ? growth / n : 0.04;
  const target = monthToRange(month).Start.slice(0, 7);
  const [ty, tm] = target.split("-").map(Number);
  const lastMonth = trend[trend.length - 1].month.split("-").map(Number);
  const ahead = (ty - lastMonth[0]) * 12 + (tm - lastMonth[1]);
  const projected = Math.round(last * Math.pow(1 + rate, Math.max(1, ahead)));
  return {
    projected,
    low: Math.round(projected * 0.88),
    high: Math.round(projected * 1.12),
    ratePct: Math.round(rate * 1000) / 10,
    monthsAhead: ahead,
    source: "growth-model",
  };
}
