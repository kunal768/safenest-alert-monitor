import React, { useEffect, useState } from "react";
import { api, onLive, pct } from "../api";
import { Chip, human } from "./Alerts.jsx";

function Confusion({ model, onClose }) {
  const e = model.eval;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(ev) => ev.stopPropagation()}>
        <h2>Evaluation · {model.name} <span className="muted">({model.ollamaTag} · {model.promptVersion})</span></h2>
        <p>
          Accuracy <strong>{pct(model.accuracy)}</strong> on {e.items} labelled descriptions · avg latency {e.avgLatencyMs} ms · error rate {pct(e.errorRate)}
        </p>
        <div className="table-wrap">
          <table className="confusion">
            <thead>
              <tr><th>expected ↓ / predicted →</th>{e.columns.map((c) => <th key={c}>{human(c)}</th>)}<th>precision</th><th>recall</th></tr>
            </thead>
            <tbody>
              {e.labels.map((row) => (
                <tr key={row}>
                  <th>{human(row)}</th>
                  {e.columns.map((col) => {
                    const v = e.confusion[row][col];
                    return <td key={col} className={v ? (row === col ? "hit" : "miss") : ""}>{v || ""}</td>;
                  })}
                  <td>{pct(e.perLabel[row].precision)}</td>
                  <td>{pct(e.perLabel[row].recall)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {e.mistakes?.length > 0 && (
          <>
            <h3>Sample mistakes</h3>
            <ul className="history">
              {e.mistakes.map((m, i) => <li key={i}>“{m.description}” — expected <strong>{human(m.expected)}</strong>, got <strong>{human(m.got)}</strong></li>)}
            </ul>
          </>
        )}
        <button className="btn" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

function DeployDialog({ models, onDeploy, onClose }) {
  const candidates = models.filter((m) => !m.isActive && m.status !== "Deploying");
  const [id, setId] = useState(candidates[0]?.id || "");
  const active = models.find((m) => m.isActive);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Deploy model version</h2>
        <label>Model version
          <select value={id} onChange={(e) => setId(e.target.value)} data-testid="deploy-select">
            {candidates.map((m) => <option key={m.id} value={m.id}>{m.name} · {m.ollamaTag} · {m.promptVersion} ({m.status})</option>)}
          </select>
        </label>
        <ol className="steps">
          <li>Check the tag exists in Ollama</li>
          <li>Warm-up prompt must return valid JSON</li>
          <li>Only then switch traffic; {active?.name || "current model"} becomes Retired (rollback-able)</li>
        </ol>
        <p className="muted">If any step fails the version is marked Failed and {active?.name || "the current model"} keeps serving.</p>
        <div className="row">
          <button className="btn big-btn" disabled={!id} onClick={() => onDeploy(id)}>Deploy</button>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

export default function Models({ staff }) {
  const [models, setModels] = useState([]);
  const [progress, setProgress] = useState({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [dialog, setDialog] = useState(false);
  const [showEval, setShowEval] = useState(null);
  const [tag, setTag] = useState("llama9:70b");
  const [tags, setTags] = useState(null);

  const load = () => api.models().then(setModels);
  useEffect(() => {
    load();
    return onLive((m) => {
      if (m.event === "eval") {
        setProgress((p) => ({ ...p, [m.payload.modelId]: m.payload.progress }));
        if (m.payload.progress === 100) load();
      }
      if (m.event === "model") load();
    });
  }, []);

  async function run(fn, okText) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      setMsg({ ok: !(r?.status === "Failed"), text: okText(r) });
    } catch (e) {
      setMsg({ ok: false, text: e.body?.message || e.message });
    }
    setBusy(false);
    load();
  }

  const deploy = (id) => {
    setDialog(false);
    run(() => api.deploy(id, { action: "deploy" }), (r) =>
      r.status === "Failed" ? `${r.name} (${r.ollamaTag}) failed its health check — previous model is still Active.` : `${r.name} is now Active.`);
  };
  const rollback = () => run(() => api.deploy("current", { action: "rollback" }), (r) => `Rolled back: ${r.name} (${r.ollamaTag}) is Active again.`);
  const evaluate = (id) => {
    setProgress((p) => ({ ...p, [id]: 0 }));
    run(() => api.evaluate(id), () => "Evaluation started — progress updates live.");
  };
  const register = () => run(() => api.registerModel(tag), (r) => `Registered ${r.ollamaTag} (${r.status}).`);
  const sync = () => run(() => api.syncModels(), (r) => (r.length ? `Registered ${r.map((m) => m.ollamaTag).join(", ")} from Ollama.` : "Registry already has every local Ollama tag."));

  const active = models.find((m) => m.isActive);
  const hasRetired = models.some((m) => m.status === "Retired");

  return (
    <div>
      <h1>AI model management</h1>
      {active && (
        <div className="card active-banner" data-testid="active-model">
          <span className="chip Active">Active</span>
          <strong>{active.name}</strong> <span className="muted">· {active.ollamaTag} · prompt {active.promptVersion}</span>
          <span className="muted"> · live avg {Math.round(active.avgLatencyMs)} ms · p95 {Math.round(active.p95LatencyMs)} ms · errors {pct(active.errorRate)}</span>
        </div>
      )}
      <p className="muted">A model version = Ollama tag + prompt version. <strong>Safe actions:</strong> Evaluate never touches traffic · Deploy is health-checked before switching · Roll back restores the previous Active version.</p>

      {staff && (
        <div className="row">
          <button className="btn big-btn" disabled={busy} onClick={() => setDialog(true)}>Deploy version…</button>
          <button className="btn warn big-btn" disabled={busy || !hasRetired} onClick={rollback}>Roll back</button>
        </div>
      )}
      {msg && <p className={msg.ok ? "oktext" : "error"} data-testid="models-msg">{msg.text}</p>}

      <div className="card table-wrap">
        <table>
          <thead>
            <tr><th>Model</th><th>Version (tag · prompt) / task</th><th>Live latency</th><th>Errors</th><th>Accuracy</th><th>State</th>{staff && <th />}</tr>
          </thead>
          <tbody>
            {models.map((m) => {
              const p = progress[m.id];
              const evaluating = p != null && p < 100;
              return (
                <tr key={m.id} data-model={m.ollamaTag}>
                  <td><strong>{m.name}</strong></td>
                  <td>{m.ollamaTag} · {m.promptVersion}<div className="muted">{m.task}</div></td>
                  <td>{m.avgLatencyMs ? `${Math.round(m.avgLatencyMs)} ms` : "—"}{m.p95LatencyMs ? <div className="muted">p95 {Math.round(m.p95LatencyMs)} ms</div> : null}</td>
                  <td>{pct(m.errorRate)}</td>
                  <td>
                    {evaluating ? (
                      <div className="progress" title={`${p}%`}><div style={{ width: `${p}%` }} /><span>{p}%</span></div>
                    ) : m.eval ? (
                      <button className="link" onClick={() => setShowEval(m)}>{pct(m.accuracy)} · details</button>
                    ) : "not evaluated"}
                  </td>
                  <td><Chip value={m.status} /></td>
                  {staff && (
                    <td><button className="btn ghost small" disabled={busy || evaluating} onClick={() => evaluate(m.id)}>Evaluate</button></td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {staff && (
        <div className="card">
          <h2>Register a model version</h2>
          <div className="row">
            <input value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Ollama tag" placeholder="e.g. llama3.2:3b" />
            <button className="btn" disabled={busy} onClick={register}>Register tag</button>
            <button className="btn ghost" disabled={busy} onClick={sync}>Sync from <code>ollama list</code></button>
            <button className="btn ghost" onClick={() => api.ollamaTags().then(setTags).catch((e) => setMsg({ ok: false, text: e.body?.message || e.message }))}>Show local tags</button>
          </div>
          {tags && <p className="muted">Ollama has: {tags.map((t) => `${t.tag}${t.registered ? " ✓" : ""}`).join(" · ") || "no models pulled"}</p>}
          <p className="muted small-print">Tip for the demo: register <code>llama9:70b</code> (not pulled) and deploy it — it ends Failed and the active model keeps serving.</p>
        </div>
      )}

      {dialog && <DeployDialog models={models} onDeploy={deploy} onClose={() => setDialog(false)} />}
      {showEval && <Confusion model={showEval} onClose={() => setShowEval(null)} />}
    </div>
  );
}
