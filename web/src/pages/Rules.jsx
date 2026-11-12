import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Chip, human } from "./Alerts.jsx";

const CHANNELS = ["push", "sms", "email"];

export default function Rules({ houseId }) {
  const [data, setData] = useState(null);
  const [msg, setMsg] = useState(null);

  async function load() {
    setData(await api.rules(houseId));
  }
  useEffect(() => { load(); }, [houseId]);

  function update(i, field, value) {
    const rules = data.rules.map((r, idx) => (idx === i ? { ...r, [field]: value } : r));
    setData({ ...data, rules });
  }

  function toggleChannel(i, ch) {
    const cur = data.rules[i].channels;
    update(i, "channels", cur.includes(ch) ? cur.filter((c) => c !== ch) : CHANNELS.filter((c) => c === ch || cur.includes(c)));
  }

  async function save() {
    setMsg(null);
    try {
      const next = await api.saveRules(houseId, {
        version: data.version,
        quietStart: data.quietStart,
        quietEnd: data.quietEnd,
        quietHoursEnabled: data.quietHoursEnabled,
        rules: data.rules,
      });
      setData(next);
      setMsg({ ok: true, text: `Saved · version ${next.version}` });
    } catch (e) {
      setMsg({ ok: false, text: e.body?.message || e.message, conflict: e.status === 409, limit: e.body?.error === "service_limit" });
    }
  }

  if (!data) return <p>Loading…</p>;
  return (
    <div>
      <h1>House alert rules · {data.houseName}</h1>
      <div className="card">
        <div className="row quiet">
          <label className="switch">
            <input type="checkbox" checked={data.quietHoursEnabled} onChange={(e) => setData({ ...data, quietHoursEnabled: e.target.checked })} />
            Quiet hours
          </label>
          <input aria-label="Quiet start" className="time" value={data.quietStart} onChange={(e) => setData({ ...data, quietStart: e.target.value })} />
          <span>to</span>
          <input aria-label="Quiet end" className="time" value={data.quietEnd} onChange={(e) => setData({ ...data, quietEnd: e.target.value })} />
          <span className="muted">{data.tz} · rules version {data.version}</span>
        </div>
        <p className="muted small-print">Quiet hours hold back <strong>safety</strong> and <strong>info</strong> alerts only. Emergency and security always go through.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Sound</th><th>On</th><th>Severity</th><th>Min confidence</th><th>Emergency high mark</th><th>Ack timeout</th><th>Channels</th></tr>
            </thead>
            <tbody>
              {data.rules.map((r, i) => (
                <tr key={r.label} data-rule={r.label}>
                  <td><strong>{human(r.label)}</strong></td>
                  <td><input type="checkbox" aria-label={`${r.label} enabled`} checked={r.enabled} onChange={(e) => update(i, "enabled", e.target.checked)} /></td>
                  <td>
                    <select value={r.severity} aria-label={`${r.label} severity`} onChange={(e) => update(i, "severity", e.target.value)}>
                      {["emergency", "security", "safety", "info"].map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>{" "}
                    <Chip value={r.severity} />
                  </td>
                  <td><input className="num" type="number" step="0.05" min="0" max="1" aria-label={`${r.label} min confidence`} value={r.minConfidence} onChange={(e) => update(i, "minConfidence", Number(e.target.value))} /></td>
                  <td><input className="num" type="number" step="0.05" min="0" max="1" value={r.highMark} disabled={r.severity !== "emergency"} onChange={(e) => update(i, "highMark", Number(e.target.value))} /></td>
                  <td><input className="num" type="number" step="10" min="10" max="3600" aria-label={`${r.label} ack timeout`} value={r.ackTimeoutSec} onChange={(e) => update(i, "ackTimeoutSec", Number(e.target.value))} /> s</td>
                  <td className="channels">
                    {CHANNELS.map((ch) => (
                      <label key={ch}><input type="checkbox" checked={r.channels.includes(ch)} onChange={() => toggleChannel(i, ch)} /> {ch}</label>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn big-btn" onClick={save}>Save rules</button>
          {msg && (
            <span className={msg.ok ? "oktext" : "error"} data-testid="rules-msg">
              {msg.limit && <strong>Service limit: </strong>}{msg.text}
              {msg.conflict && <button className="btn ghost small" onClick={() => { load(); setMsg(null); }}>Reload</button>}
            </span>
          )}
        </div>
        <p className="muted small-print">Limits: emergency rules cannot be disabled and need a channel · minimum confidence floor 0.5 · emergency below the high mark is sent as security · ack timeout 10–3600 s (emergency ≤ 300 s); the first message goes to the priority-1 contact, escalation follows after the timeout.</p>
      </div>
    </div>
  );
}
