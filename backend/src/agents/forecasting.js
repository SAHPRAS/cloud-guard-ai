import { invokeClaude } from "../bedrock/bedrockClient.js";
import {
  getMonthlyTrend,
  getCostForecast,
  growthModelForecast,
  forecastByService,
} from "../tools/costExplorerTools.js";

const SYSTEM = `You are the Forecasting agent.
You project future AWS spend from historical actuals, broken down by service.
Explain the main drivers and state confidence honestly.
Confidence should decrease the further into the future the forecast extends.`;

export async function runForecasting({ month, region }) {
  const trend = await getMonthlyTrend({ months: 24, region });
  const aws = await getCostForecast({ month, region });         // AWS ML total forecast
  const model = growthModelForecast(trend, month);             // explainable total model
  const byService = await forecastByService({ month, region }); // per-service + sum

  const res = await invokeClaude({
    system: SYSTEM,
    messages: [
      {
        role: "user",
        content: `Forecast spend for ${month}.
Per-service projection: ${JSON.stringify(byService.services.slice(0, 8))}.
Projected total: $${byService.total}.
AWS forecast: ${JSON.stringify(aws)}.
Give a final projection, the top driver services, a range, and a confidence %.`,
      },
    ],
    maxTokens: 700,
  });

  const confidence = Math.max(40, 92 - byService.monthsAhead * 7);

  return {
    summary: res.content?.find((b) => b.type === "text")?.text || "",
    // structured data for the per-service table in the UI:
    services: byService.services, // [{service, projected, low, high}]
    total: byService.total,
    low: byService.low,
    high: byService.high,
    monthsAhead: byService.monthsAhead,
    confidence,
    aws,
    model,
  };
}
