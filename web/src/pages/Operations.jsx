import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmtTime } from "../api";
import { human } from "./Alerts.jsx";

const SCENARIOS = [
  { id: "glass", n: 1, label: "Glass break (kitchen)", cls: "danger", fn: () => api.scenario("glass") },
  { id: "fall", n: 2, label: "Fall (escalation)", cls: "danger", fn: () => api.scenario("fall") },
  { id: "dup", n: 3, label: "Same eventId twice", cls: "", fn: () => api.duplicate() },
  { id: "coughs", n: 3, label: "10 coughs (merge)", cls: "", fn: () => api.coughs() },
  { id: "night", n: 4, label: "Cough at 02:00", cls: "ghost", fn: () => api.scenario("cough_night") },
  { id: "ambiguous", n: 5, label: "Ambiguous text", cls: "ghost", fn: () => api.scenario("ambiguous") },
  { id: "failloud", n: 7, label: "Fail loud (LLM outage)", cls: "warn", fn: () => api.scenario("fail_loud") },
  { id: "pattern", n: 8, label: "Run pattern job", cls: "", fn: () => api.runPatterns("H-1042") },
  { id: "device", n: 8, label: "Device D-210 missed", cls: "", fn: () => api.missed("D-210") },
];

function Result({ r }) {
  if (!r) return null;
  if (r.error) return <div className="result error">{r.message || r.error}</div>;
  const ev = r.first || r;
  return (
    <div className="result" data-testid="sim-result">
      {r.first ? (
        <p>Sent <strong>{r.eventId}</strong> twice → first duplicate={String(r.first.duplicate)}, second duplicate=<strong>{String(r.second.duplicate)}</strong>; one alert, count <strong>{r.alertCount}</strong>.</p>
      ) : r.count != null ? (
        <p>10 cough events → one alert, count <strong>{r.count}</strong>.</p>
      ) : r.created ? (
        <p>Pattern job created {r.created.length} alert(s){r.created.map((a) => `: ${human(a.type)}`)}.</p>
      ) : "alert" in r ? (
        <p>{r.alert ? `Device warning raised: ${r.alert.reason}` : r.message}</p>
      ) : (
        <>
          <p>
            AI label <strong>{ev.label ? human(ev.label) : "— unclassified —"}</strong>
            {ev.confidence != null && <> · confidence <strong>{Math.round(ev.confidence * 100)}%</strong></>}
            {ev.modelVersion && <> · model {ev.modelVersion}</>}{ev.latencyMs != null && <> · {ev.latencyMs} ms</>}
          </p>
          <p className="muted">Reason: {ev.reason}</p>
          <p>{ev.alertId ? <>Alert <strong>{ev.alertId}</strong> raised.</> : <strong>No alert (ignored or suppressed by house rules).</strong>}</p>
        </>
      )}
    </div>
  );
}

export default function Operations() {
  const [features, setFeatures] = useState(null);
  const [health, setHealth] = useState(null);
  const [custom, setCustom] = useState("someone is dragging furniture and laughing");
  const [db, setDb] = useState(70);
  const [device, setDevice] = useState("D-210");
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState("");
  const [follow, setFollow] = useState(true);
  const [audit, setAudit] = useState([]);
  const [houses, setHouses] = useState([]);
  const nav = useNavigate();

  const refresh = () => {
    api.features().then(setFeatures);
    api.health().then(setHealth);
    api.audit(12).then(setAudit);
  };
  useEffect(() => { refresh(); api.houses().then(setHouses); }, []);

  async function run(id, fn) {
    setRunning(id);
    setResult(null);
    try {
      const r = await fn();
      setResult(r);
      const alertId = r.alertId || r.created?.[0]?.alertId || r.alert?.alertId;
      if (follow && alertId) nav(`/alerts/${alertId}`);
    } catch (e) {
      setResult(e.body || { error: e.message });
    }
    setRunning("");
    refresh();
  }

  async function toggleOutage() {
    await api.outage(!features.aiOutage);
    refresh();
  }

  const devices = houses.find((h) => h.id === "H-1042")?.devices || [];

  return (
    <div>
      <h1>Operations / Simulator</h1>
      <p className="muted">The simulator acts as the home gateway. It sends a <strong>text description of the sound</strong> (what an on-device audio front end reports), not raw audio. The local LLM in Ollama classifies that text.</p>
      <div className="card">
        <div className="row spread">
          <h2>Demo scenarios</h2>
          <label className="switch"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Open the alert after sending</label>
        </div>
        <div className="scenario-grid">
          {SCENARIOS.map((s) => (
            <button key={s.id} data-scenario={s.id} className={`btn big-btn ${s.cls}`} disabled={!!running} onClick={() => run(s.id, s.fn)}>
              <span className="num-badge">{s.n}</span>{running === s.id ? "Running…" : s.label}
            </button>
          ))}
        </div>
        <h2>Custom event</h2>
        <div className="row">
          <textarea rows={2} className="grow" aria-label="Sound description" value={custom} onChange={(e) => setCustom(e.target.value)} />
        </div>
        <div className="row">
          <label>Device <select value={device} onChange={(e) => setDevice(e.target.value)}>
            {devices.map((d) => <option key={d.id} value={d.id}>{d.id} · {d.room}</option>)}
          </select></label>
          <label>Level <input className="num" type="number" value={db} onChange={(e) => setDb(Number(e.target.value))} /> dB</label>
          <button className="btn big-btn" data-scenario="custom" disabled={!!running} onClick={() => run("custom", () => api.custom({ description: custom, levelDb: db, deviceId: device }))}>Send custom event</button>
        </div>
        <Result r={result} />
      </div>

      <div className="ops-grid">
        <div className="card">
          <h2>AI service</h2>
          <p>Adapter <strong>{features?.MODEL_ADAPTER}</strong> · active model <strong>{health?.activeModel}</strong> · Ollama reachable <strong>{String(health?.ollama)}</strong></p>
          <label className="switch">
            <input type="checkbox" checked={!!features?.aiOutage} onChange={toggleOutage} data-testid="outage-toggle" />
            Simulate LLM outage (classifier calls go to a closed port)
          </label>
          <p className="muted small-print">While on, events come back unclassified; loud ones (&gt; 85 dB) still raise a warning.</p>
        </div>

        <div className="card cloud" data-testid="cloud-card">
          <h2>Cloud deployment</h2>
          <p className="cloud-status"><span className="chip Registered">Implemented</span> <span className="chip false_alarm">Disabled (feature flag)</span></p>
          <p className="muted">
            ENABLE_CLOUD_DEPLOY = <strong>{String(features?.ENABLE_CLOUD_DEPLOY)}</strong> · notifier {features?.NOTIFIER} · escalation timeout {features?.escalationTimeoutSec}s
          </p>
          <p>Dockerfile, <code>deploy/docker-compose.prod.yml</code>, Terraform (EC2 + security group + ALB health check on <code>/health</code>) and a GitHub Actions workflow with a gated deploy job. <code>make cloud-check</code> validates them without applying anything. Ollama stays local; a cloud host for it would need a GPU or large CPU instance.</p>
        </div>
      </div>

      <div className="card">
        <div className="row spread"><h2>Audit log (latest)</h2><button className="btn ghost small" onClick={refresh}>Refresh</button></div>
        <ul className="history">
          {audit.map((a, i) => (
            <li key={i}><span className="muted">{fmtTime(a.ts)}</span> <strong>{a.action}</strong> {a.entity} {a.entityId} — {a.detail} <span className="muted">· {a.actor}</span></li>
          ))}
        </ul>
      </div>
    </div>
  );
}
