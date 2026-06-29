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
];

const TARGETS = [
  { id: "full", label: "Full scan", icon: "ti-radar-2", full: true },
  { id: "cost", label: "Cost", icon: "ti-coin" },
  { id: "anomaly", label: "Anomaly", icon: "ti-activity" },
  { id: "rightsizing", label: "Rightsizing", icon: "ti-resize" },
  { id: "forecast", label: "Forecast", icon: "ti-chart-line" },
  { id: "security", label: "Security", icon: "ti-lock" },
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
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState([]);
  const [chatLoading, setChatLoading] = useState(false);

  const month = months[monthIdx];
  const future = month.future;
  const isPast = monthIdx < presentIdx;

  const canvasRef = useRef(null);
  const animRef = useRef({ angle: 0, blips: [], scanning: false });

  // forecasting a closed month is meaningless — fall back if the user picks one while forecast is selected
  useEffect(() => {
    if (target === "forecast" && isPast) setTarget("full");
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

  async function sendChat() {
    const q = chatInput.trim();
    if (!q || chatLoading) return;
    setChatMessages((m) => [...m, { role: "user", text: q }]);
    setChatInput("");
    setChatLoading(true);
    try {
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, month: month.iso, region }),
      });
      const data = await res.json();
      setChatMessages((m) => [
        ...m,
        { role: "ai", text: data.summary || data.error || "(no response)", trace: data.trace },
      ]);
    } catch (e) {
      setChatMessages((m) => [...m, { role: "ai", text: `Error: ${e.message}` }]);
    } finally {
      setChatLoading(false);
    }
  }

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
            const blocked = t.id === "forecast" && isPast;
            return (
              <div
                key={t.id}
                className={`sm-item ${t.full ? "full" : ""} ${target === t.id ? "on" : ""} ${blocked ? "disabled" : ""}`}
                title={blocked ? `${month.label} has already ended — forecast isn't available for past months` : undefined}
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
          {renderCostTable(result)}
          {Object.entries(result.blocks || {}).map(([k, v]) => (
            v.summary ? (
              <div key={k} className="rep-block">
                <div className="rep-title"><i className="ti ti-file-text" /> {k} — analysis</div>
                <div className="rep-summary">{v.summary}</div>
                {renderTrace(v.trace)}
              </div>
            ) : null
          ))}
        </div>
      )}

      <div className="chat-panel">
        <div className="rep-title"><i className="ti ti-message-circle" /> Ask Cloud Guard AI</div>
        <div className="chat-messages">
          {chatMessages.map((m, i) => (
            <div key={i} className={`chat-msg ${m.role}`}>
              <div className="chat-msg-text">{m.text}</div>
              {m.role === "ai" && renderTrace(m.trace)}
            </div>
          ))}
          {chatLoading && <div className="chat-msg ai chat-loading">thinking…</div>}
        </div>
        <div className="chat-input-row">
          <input
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && sendChat()}
            placeholder="e.g. why did my bill go up and is anything insecure?"
            disabled={chatLoading}
          />
          <button onClick={sendChat} disabled={chatLoading}><i className="ti ti-send" /></button>
        </div>
      </div>
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

  if (future) {
    const f = result.blocks?.forecast;
    if (!f || !f.services) return null;
    rows = f.services.map((s) => ({ name: s.service, amount: s.projected, lo: s.low, hi: s.high }));
    total = f.total;
    title = `Forecasted cost by service — ${result.month} (${f.confidence}% confidence)`;
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
      <div className="rep-title">
        <i className={`ti ${future ? "ti-chart-dots" : "ti-chart-bar"}`} /> {title}
      </div>
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
