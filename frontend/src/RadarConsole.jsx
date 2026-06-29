import React, { useEffect, useRef, useState } from "react";

const REGIONS = [
  "eu-central-1 · Frankfurt", "us-east-1 · N.Virginia", "us-east-2 · Ohio",
  "us-west-1 · N.California", "us-west-2 · Oregon", "ap-south-1 · Mumbai",
  "ap-southeast-1 · Singapore", "ap-northeast-1 · Tokyo", "eu-west-1 · Ireland",
  "eu-west-2 · London", "sa-east-1 · Sao Paulo", "ALL REGIONS",
];

const AGENTS = [
  { id: "cost", label: "cost_analyst" },
  { id: "anomaly", label: "anomaly_det" },
  { id: "rightsizing", label: "rightsizing" },
  { id: "forecast", label: "forecasting" },
  { id: "security", label: "security" },
  { id: "resources", label: "resource_aud" },
];

const TARGETS = [
  { id: "full", label: "Full scan", icon: "ti-radar-2", full: true },
  { id: "cost", label: "Cost", icon: "ti-coin" },
  { id: "anomaly", label: "Anomaly", icon: "ti-activity" },
  { id: "rightsizing", label: "Rightsizing", icon: "ti-resize" },
  { id: "forecast", label: "Forecast", icon: "ti-chart-line" },
  { id: "security", label: "Security", icon: "ti-lock" },
  { id: "resources", label: "Resources", icon: "ti-stack-2" },
];

// build month list: 24 months of history .. 6 months of forecast horizon, anchored on today's real month
function buildMonths() {
  const out = [];
  const present = new Date();
  present.setDate(1);
  const d = new Date(present);
  d.setMonth(d.getMonth() - 24);
  const end = new Date(present);
  end.setMonth(end.getMonth() + 6);
  let i = 0;
  while (d <= end) {
    const label = d.toLocaleString("en", { month: "short" }).toUpperCase() + " " + String(d.getFullYear()).slice(2);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    out.push({ label, iso, future: d > present, idx: i++ });
    d.setMonth(d.getMonth() + 1);
  }
  return out;
}

function currentMonthIso() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export default function RadarConsole() {
  const months = buildMonths();
  const presentIdx = months.findIndex((m) => m.iso === currentMonthIso());

  const [monthIdx, setMonthIdx] = useState(presentIdx);
  const [region, setRegion] = useState(REGIONS[0]);
  const [target, setTarget] = useState("full");
  const [scanning, setScanning] = useState(false);
  const [identity, setIdentity] = useState(null);
  const [feed, setFeed] = useState([]);
  const [agentState, setAgentState] = useState({});
  const [result, setResult] = useState(null);

  const month = months[monthIdx];
  const future = month.future;
  const isPast = monthIdx < presentIdx;

  const canvasRef = useRef(null);
  const animRef = useRef({ angle: 0, blips: [], scanning: false });

  // forecasting a closed month / inventorying a future month are both meaningless — fall back
  useEffect(() => {
    if (target === "forecast" && isPast) setTarget("full");
    if (target === "resources" && future) setTarget("full");
  }, [monthIdx]);

  // ----- identity bar -----
  useEffect(() => {
    fetch("/api/identity").then((r) => r.json()).then(setIdentity).catch(() => {});
  }, []);

  // ----- radar draw loop -----
  useEffect(() => {
    const cv = canvasRef.current;
    const ctx = cv.getContext("2d");
    const W = 300, H = 300, cx = W / 2, cy = H / 2, R = 132;
    let raf;
    const tick = () => {
      const a = animRef.current;
      const col = future ? "133,183,235" : "61,220,132";
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = "#06120e"; ctx.fillRect(0, 0, W, H);
      ctx.strokeStyle = `rgba(${col},0.18)`; ctx.lineWidth = 1;
      for (let i = 1; i <= 4; i++) { ctx.beginPath(); ctx.arc(cx, cy, (R * i) / 4, 0, Math.PI * 2); ctx.stroke(); }
      ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy); ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke();
      if (a.scanning) {
        for (let i = 0; i < 55; i++) {
          const ang = a.angle - i * 0.013;
          ctx.strokeStyle = `rgba(${col},${0.26 * (1 - i / 55)})`; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(ang) * R, cy + Math.sin(ang) * R); ctx.stroke();
        }
      }
      a.blips.forEach((b) => {
        const bx = cx + Math.cos(b.a) * b.r, by = cy + Math.sin(b.a) * b.r;
        const diff = Math.abs(((a.angle - b.a) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2));
        if (a.scanning && diff < 0.09) b.lit = 1;
        if (b.lit > 0.02) {
          const c = b.type === "crit" ? "#ff5f56" : b.type === "warn" ? "#ffbd2e" : future ? "#85b7eb" : "#3ddc84";
          ctx.globalAlpha = b.lit; ctx.fillStyle = c; ctx.shadowColor = c; ctx.shadowBlur = 10;
          ctx.beginPath(); ctx.arc(bx, by, 3, 0, Math.PI * 2); ctx.fill();
          ctx.shadowBlur = 0; ctx.globalAlpha = b.lit * 0.85; ctx.font = "8px monospace"; ctx.fillStyle = c;
          ctx.fillText(b.label, bx + 6, by + 3); ctx.globalAlpha = 1; b.lit *= 0.974;
        }
      });
      const cc = future ? "#85b7eb" : "#3ddc84";
      ctx.fillStyle = cc; ctx.shadowColor = cc; ctx.shadowBlur = 8;
      ctx.beginPath(); ctx.arc(cx, cy, 3, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
      if (a.scanning) a.angle += 0.024;
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [future]);

  function seedBlips() {
    const services = ["EKS", "EC2", "DocDB", "NAT", "S3", "CW", "IAM", "SG", "GD", "EBS"];
    animRef.current.blips = services.map((s) => ({
      a: Math.random() * Math.PI * 2,
      r: 36 + Math.random() * 90,
      type: future ? "proj" : Math.random() > 0.8 ? "crit" : Math.random() > 0.6 ? "warn" : "ok",
      label: s, lit: 0,
    }));
  }

  function pushFeed(type, msg) {
    setFeed((f) => [...f, { type, msg, t: new Date().toLocaleTimeString("en", { hour12: false }) }]);
  }

  async function runScan() {
    if (scanning) return;
    setScanning(true);
    setResult(null);
    setFeed([]);
    setAgentState({});
    seedBlips();
    animRef.current.scanning = true;
    animRef.current.angle = 0;

    pushFeed("info", `${future ? "Forecast" : target.toUpperCase()} started — ${month.label} · ${region.split(" ·")[0]}`);
    if (identity?.account) pushFeed("info", `Authenticated as ${identity.profile} (${identity.account})`);
    if (future) pushFeed("fc", "Future month — projection mode engaged");

    try {
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target, month: month.iso, region }),
      });
      const data = await res.json();

      // animate agent completion based on what came back
      const keys = Object.keys(data.blocks || {});
      pushFeed("ok", `Backend responded — ${keys.length} agent block(s)`);
      keys.forEach((k) => {
        setAgentState((s) => ({ ...s, [k]: "done" }));
        const block = data.blocks[k];
        if (block.error) pushFeed("warn", `${k}: ${block.error}`);
        else pushFeed("ok", `${k} complete`);
        (block.trace || []).forEach((t) => {
          const args = Object.keys(t.input || {}).join(", ");
          pushFeed("ai", `${k}: called ${t.tool}(${args})`);
        });
      });
      setResult(data);
      pushFeed(future ? "fc" : "ok", future ? "Projection ready" : "Sweep complete");
    } catch (e) {
      pushFeed("crit", `Scan failed: ${e.message}`);
    } finally {
      animRef.current.scanning = false;
      setScanning(false);
    }
  }

  const feedIcon = { info: "ti-chevron-right", ok: "ti-circle-check", warn: "ti-alert-triangle", crit: "ti-alert-octagon", fc: "ti-chart-dots", ai: "ti-cpu" };

  return (
    <div className={`console ${future ? "fc-mode" : ""}`}>
      <div className="con-top">
        <span className="con-title">CLOUD_GUARD::RADAR</span>
        <div className="deck">
          <select value={monthIdx} onChange={(e) => setMonthIdx(+e.target.value)}>
            <optgroup label="historical (actuals)">
              {months.filter((m) => !m.future).map((m) => <option key={m.idx} value={m.idx}>{m.label}</option>)}
            </optgroup>
            <optgroup label="forecast (projected)">
              {months.filter((m) => m.future).map((m) => <option key={m.idx} value={m.idx}>{m.label}</option>)}
            </optgroup>
          </select>
          <select value={region} onChange={(e) => setRegion(e.target.value)}>
            {REGIONS.map((r) => <option key={r}>{r}</option>)}
          </select>
        </div>
      </div>

      <div className="id-bar">
        <div className="id-block"><span className="id-l">Profile</span><span className="id-v">{identity?.profile || "—"}</span></div>
        <div className="id-block"><span className="id-l">Account</span><span className="id-v">{identity?.account || "—"}</span></div>
        <div className="id-block"><span className="id-l">Role</span><span className="id-v">{identity?.role || "—"}</span></div>
        <span className={`id-tag ${future ? "fc" : ""}`}>{future ? "FORECAST" : "LIVE SCAN"}</span>
      </div>

      <div className="con-body">
        <div className="scanmenu">
          <div className="sm-h">Scan target</div>
          {TARGETS.map((t) => {
            const blocked =
              (t.id === "forecast" && isPast) || (t.id === "resources" && future);
            const blockedReason =
              t.id === "forecast" && isPast
                ? `${month.label} has already ended — forecast isn't available for past months`
                : t.id === "resources" && future
                ? "Resource inventory shows what's running now — not available for future months"
                : undefined;
            return (
              <div
                key={t.id}
                className={`sm-item ${t.full ? "full" : ""} ${target === t.id ? "on" : ""} ${blocked ? "disabled" : ""}`}
                title={blockedReason}
                onClick={() => !scanning && !blocked && setTarget(t.id)}
              >
                <i className={`ti ${t.icon}`} /> {t.label}
              </div>
            );
          })}
        </div>

        <div className="radar-wrap">
          <canvas ref={canvasRef} width={300} height={300} className="radar-canvas" />
        </div>

        <div className="side">
          <div className="side-sec">
            <div className="side-h">Agent array</div>
            {AGENTS.map((a) => (
              <div key={a.id} className="ag-line">
                <span className={`ag-bullet ${agentState[a.id] === "done" ? "done" : ""}`} />
                {a.label}
              </div>
            ))}
          </div>
          <div className="side-sec feed-sec">
            <div className="side-h">Live feed</div>
            <div className="feed">
              {feed.map((f, i) => (
                <div key={i} className={`fr ${f.type}`}>
                  <div className="fr-ic"><i className={`ti ${feedIcon[f.type]}`} /></div>
                  <div><div className="fr-msg">{f.msg}</div><div className="fr-time">{f.t}</div></div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="go-bar">
        <span className="go-target">TARGET: <b>{target.toUpperCase()}</b></span>
        <button className="scan-go" onClick={runScan} disabled={scanning}>
          <i className="ti ti-radar-2" /> {scanning ? "SCANNING" : "INITIATE"}
        </button>
      </div>

      {result && (
        <div className="results">
          {renderSynthesis(result.synthesis)}
          {renderCostTable(result)}
          {result.mode === "forecast" && <ForecastChart forecast={result.blocks?.forecast} />}
          {renderResourceBlock(result.blocks?.resources)}
          {Object.entries(result.blocks || {}).map(([k, v]) => {
            if (k === "resources") return null; // rendered above via renderResourceBlock
            return v.summary ? (
              <div key={k} className="rep-block">
                {bedrockTitle("ti-file-text", `${k} — analysis`)}
                <div className="rep-summary">{v.summary}</div>
                {k === "anomaly" && renderAnomalyFindings(v)}
                {renderTrace(v.trace)}
              </div>
            ) : null;
          })}
        </div>
      )}
    </div>
  );
}

// Every block of Claude-generated text/suggestions renders its title through this —
// so it's always clear which parts of the page are raw AWS data vs. Claude Bedrock output.
function bedrockTitle(icon, label) {
  return (
    <div className="rep-title">
      <i className={`ti ${icon}`} /> <span className="bedrock-tag">Claude Bedrock</span> {label}
    </div>
  );
}

// Shared ranked list for any agent's {title, category, impact, detail} findings —
// every agent's Claude-generated suggestions render through this one component.
function renderPriorityList(items) {
  if (!items || items.length === 0) return null;
  const impactRank = { high: 0, medium: 1, low: 2 };
  const sorted = [...items].sort((a, b) => (impactRank[a.impact] ?? 3) - (impactRank[b.impact] ?? 3));
  return (
    <ol className="syn-priorities">
      {sorted.map((p, i) => (
        <li key={i} className={`syn-pri syn-pri-${p.impact}`}>
          <span className={`syn-impact syn-impact-${p.impact}`}>{p.impact}</span>
          <span className="syn-cat">{p.category}</span>
          <div className="syn-pri-body">
            <div className="syn-pri-title">{p.title}</div>
            <div className="syn-pri-detail">{p.detail}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

// Cross-agent executive analysis — connects findings across cost/security/rightsizing/forecast.
function renderSynthesis(synthesis) {
  if (!synthesis || (!synthesis.headline && !synthesis.narrative)) return null;
  return (
    <div className="rep-block synthesis-block">
      {bedrockTitle("ti-sparkles", "Executive analysis")}
      {synthesis.headline && <div className="syn-headline">{synthesis.headline}</div>}
      {synthesis.narrative && <div className="rep-summary">{synthesis.narrative}</div>}
      {renderPriorityList(synthesis.priorities)}
    </div>
  );
}

// Anomaly Detector's Claude-judged findings — severity, driver service, fix suggestion.
function renderAnomalyFindings(block) {
  const findings = block?.findings;
  if (!findings || findings.length === 0) return null;
  const items = findings.map((f) => ({
    impact: f.severity === "crit" ? "high" : "medium",
    category: f.driverService || f.month,
    title: `${f.month}${f.changePct != null ? ` (+${f.changePct}%)` : ""}`,
    detail: [f.explanation, f.suggestion].filter(Boolean).join(" → ") || "Flagged by heuristic — no AI explanation yet.",
  }));
  return renderPriorityList(items);
}

// Resource Auditor: the raw inventory table is factual AWS data; the fixes below it
// are Claude's judgment call, so the two get separate blocks/headings.
function renderResourceBlock(block) {
  if (!block || (!block.resources?.length && !block.errors)) return null;
  const findingItems = (block.findings || []).map((f) => ({
    impact: f.severity,
    category: f.type,
    title: f.resource,
    detail: [f.issue, f.suggestion].filter(Boolean).join(" → "),
  }));
  const byType = {};
  (block.resources || []).forEach((r) => {
    (byType[r.type] = byType[r.type] || []).push(r);
  });

  const rowClass = (r) =>
    r.severity === "high" ? "res-row-critical" : r.severity === "medium" ? "res-row-flagged" : r.severity === "low" ? "res-row-low" : "";

  return (
    <>
      <div className="rep-block">
        <div className="rep-title">
          <i className="ti ti-stack-2" /> Resource inventory
          {block.counts && <span className="res-count-badge">{block.counts.total} resources · {block.counts.flagged} flagged</span>}
        </div>
        {Object.entries(byType).map(([type, items]) => (
          <table key={type} className="cost-table res-table">
            <thead>
              <tr><th>{type.toUpperCase()}</th><th>Status</th><th style={{ textAlign: "right" }}>Flags</th></tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} className={rowClass(r)}>
                  <td className="ct-name">{r.name}<div className="res-detail">{r.detail}</div></td>
                  <td><span className={`res-status res-status-${r.severity || "ok"}`}>{r.status}</span></td>
                  <td className="ct-amt">{(r.flags || []).join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
        {block.errors && Object.keys(block.errors).length > 0 && (
          <div className="res-errors">
            {Object.entries(block.errors).map(([cat, err]) => (
              <div key={cat} className="res-error-line">{cat}: {err.hint || err.error}</div>
            ))}
          </div>
        )}
      </div>
      {(findingItems.length > 0 || block.summary) && (
        <div className="rep-block">
          {bedrockTitle("ti-shield-check", "Resource fixes")}
          {block.summary && <div className="rep-summary">{block.summary}</div>}
          {renderPriorityList(findingItems)}
        </div>
      )}
    </>
  );
}

// Forecast visualization — trailing actuals as a line, projected month as a banded point.
function ForecastChart({ forecast }) {
  if (!forecast || !forecast.trendHistory || forecast.trendHistory.length === 0) return null;
  const W = 640, H = 170, padL = 50, padR = 70, padT = 16, padB = 24;
  const innerW = W - padL - padR, innerH = H - padT - padB;

  const history = forecast.trendHistory;
  const points = [...history.map((h) => ({ label: h.month, amount: h.amount })), { label: "proj", amount: forecast.total, projected: true }];

  const maxY = Math.max(forecast.high || 0, ...points.map((p) => p.amount)) * 1.08;
  const x = (i) => padL + (i / (points.length - 1)) * innerW;
  const y = (v) => padT + innerH - (v / maxY) * innerH;

  const linePath = points
    .filter((p) => !p.projected)
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.amount)}`)
    .join(" ");

  const lastIdx = points.length - 2;
  const projIdx = points.length - 1;
  const fmt = (n) => `$${Math.round(n).toLocaleString()}`;

  return (
    <div className="rep-block">
      {bedrockTitle("ti-chart-dots", `Spend forecast (${forecast.confidence}% confidence)`)}
      <svg viewBox={`0 0 ${W} ${H}`} className="forecast-chart">
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <line key={f} x1={padL} x2={W - padR} y1={padT + innerH * f} y2={padT + innerH * f} className="fc-grid" />
        ))}
        {/* low–high band for the projected month */}
        <line x1={x(projIdx)} x2={x(projIdx)} y1={y(forecast.low)} y2={y(forecast.high)} className="fc-band" />
        {/* connector from last actual to projection */}
        <line x1={x(lastIdx)} y1={y(points[lastIdx].amount)} x2={x(projIdx)} y2={y(forecast.total)} className="fc-connector" />
        <path d={linePath} className="fc-line" />
        {points.map((p, i) =>
          p.projected ? (
            <circle key={i} cx={x(i)} cy={y(forecast.total)} r="4.5" className="fc-dot-proj" />
          ) : (
            <circle key={i} cx={x(i)} cy={y(p.amount)} r="2.5" className="fc-dot" />
          )
        )}
        <text x={x(projIdx)} y={y(forecast.total) - 12} textAnchor="middle" className="fc-label-proj">{fmt(forecast.total)}</text>
        <text x={x(lastIdx)} y={y(points[lastIdx].amount) - 10} textAnchor="middle" className="fc-label">{fmt(points[lastIdx].amount)}</text>
        <text x={padL} y={H - 6} className="fc-axis">{history[0]?.month}</text>
        <text x={W - padR} y={H - 6} textAnchor="end" className="fc-axis">{history[history.length - 1]?.month}</text>
        <text x={x(projIdx)} y={H - 6} textAnchor="end" className="fc-axis fc-axis-proj">projected</text>
      </svg>
    </div>
  );
}

// Collapsible "AI reasoning" panel — shows the actual tool calls an agent made.
function renderTrace(trace) {
  if (!trace || trace.length === 0) return null;
  return (
    <details className="ai-trace">
      <summary>AI reasoning ({trace.length} step{trace.length !== 1 ? "s" : ""})</summary>
      <ol className="ai-trace-list">
        {trace.map((t, i) => (
          <li key={i}>
            <span className="trace-tool">{t.tool}</span>
            <span className="trace-input">({Object.entries(t.input || {}).map(([k, v]) => `${k}=${v}`).join(", ")})</span>
            <span className="trace-arrow">→</span>
            <span className="trace-output">{summarizeOutput(t.output)}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function summarizeOutput(output) {
  if (output == null) return "—";
  if (Array.isArray(output)) return `${output.length} item(s)`;
  if (typeof output === "object") {
    if (output.error) return `error: ${output.error}`;
    const s = JSON.stringify(output);
    return s.length > 140 ? s.slice(0, 140) + "…" : s;
  }
  return String(output);
}

// Renders the per-service cost (historical) or per-service forecast (future) table.
function renderCostTable(result) {
  const future = result.mode === "forecast";
  let rows = [];
  let total = 0;
  let title = "";
  let isBedrock = false;

  if (future) {
    const f = result.blocks?.forecast;
    if (!f || !f.services) return null;
    rows = f.services.map((s) => ({ name: s.service, amount: s.projected, lo: s.low, hi: s.high }));
    total = f.total;
    isBedrock = !!f.aiGenerated;
    title = `${f.aiGenerated ? "AI-forecasted" : "Forecasted"} cost by service — ${result.month} (${f.confidence}% confidence)`;
  } else {
    const c = result.blocks?.cost;
    if (!c || !c.data?.services) return null;
    rows = c.data.services.map((s) => ({ name: s.service, amount: s.amount, usageCost: s.usageCost, discount: s.discount }));
    total = c.data.total;
    title = `Cost by service — ${result.month}`;
  }

  const comparison = !future ? result.blocks?.cost?.comparison : null;
  const hasBreakdown = !future && rows.length > 0 && rows[0].usageCost !== undefined;
  const max = Math.max(...rows.map((r) => r.amount), 1);
  const barColor = future ? "#85b7eb" : "#3ddc84";
  const fmt = (n) => `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  return (
    <div className="rep-block">
      {isBedrock
        ? bedrockTitle(future ? "ti-chart-dots" : "ti-chart-bar", title)
        : <div className="rep-title"><i className={`ti ${future ? "ti-chart-dots" : "ti-chart-bar"}`} /> {title}</div>}
      {comparison && (
        <div className={`mom-badge ${comparison.delta >= 0 ? "up" : "down"}`}>
          <i className={`ti ${comparison.delta >= 0 ? "ti-trending-up" : "ti-trending-down"}`} />
          vs {comparison.previousMonth}: ${comparison.previousTotal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          {" "}({comparison.delta >= 0 ? "+" : ""}${comparison.delta.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          {comparison.deltaPct !== null && `, ${comparison.delta >= 0 ? "+" : ""}${comparison.deltaPct}%`})
        </div>
      )}
      <table className="cost-table">
        <thead>
          <tr>
            <th>Service</th>
            <th></th>
            {hasBreakdown && <th style={{ textAlign: "right" }}>Usage Cost</th>}
            <th style={{ textAlign: "right" }}>{future ? "Projected" : hasBreakdown ? "Actual Cost" : "Cost"}</th>
            {hasBreakdown && <th style={{ textAlign: "right" }}>Discount</th>}
            {future && <th style={{ textAlign: "right" }}>Range</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td className="ct-name">{r.name}</td>
              <td className="ct-bar">
                <div className="ct-track">
                  <div className="ct-fill" style={{ width: `${(r.amount / max) * 100}%`, background: barColor }} />
                </div>
              </td>
              {hasBreakdown && <td className="ct-amt">{fmt(r.usageCost)}</td>}
              <td className="ct-amt">{fmt(r.amount)}</td>
              {hasBreakdown && <td className="ct-amt ct-discount">{r.discount > 0 ? `-${fmt(r.discount)}` : fmt(r.discount)}</td>}
              {future && <td className="ct-range">${r.lo.toLocaleString()}–${r.hi.toLocaleString()}</td>}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td className="ct-total-lbl" colSpan={2}>{future ? "PROJECTED TOTAL" : "TOTAL"}</td>
            {hasBreakdown && <td></td>}
            <td className="ct-total" style={{ color: barColor }}>{fmt(total)}</td>
            {hasBreakdown && <td></td>}
            {future && <td></td>}
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
