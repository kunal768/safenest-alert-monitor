import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtTime, onLive, pct } from "../api";

const LABELS = { false_alarm: "false alarm", no_activity_by_noon: "no activity by noon", routine_changed: "routine changed", device_offline: "device offline", glass_break: "glass break", call_for_help: "call for help", door_open: "door open", unknown_voice: "unknown voice", long_silence: "long silence" };
export const human = (v) => LABELS[v] || v;

export function Chip({ value }) {
  return <span className={`chip ${value}`}>{human(value)}</span>;
}

const TYPES = ["glass_break", "fall", "scream", "call_for_help", "cough", "door_open", "unknown_voice", "long_silence", "unclassified", "no_activity_by_noon", "routine_changed", "device_offline"];
const SIZE = 15;

export default function Alerts() {
  const [data, setData] = useState({ items: [], total: 0 });
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(1);
  const [fresh, setFresh] = useState(new Set());
  const nav = useNavigate();

  async function load() {
    const q = new URLSearchParams({ page, size: SIZE });
    if (severity) q.set("severity", severity);
    if (status) q.set("status", status);
    if (type) q.set("type", type);
    setData(await api.alerts(`?${q}`));
  }

  useEffect(() => {
    load();
    return onLive((m) => {
      if (m.event !== "alert") return;
      load();
      const id = m.payload?.alertId;
      if (id) {
        setFresh((s) => new Set(s).add(id));
        setTimeout(() => setFresh((s) => { const n = new Set(s); n.delete(id); return n; }), 4000);
      }
    });
  }, [severity, status, type, page]);

  const pages = Math.max(1, Math.ceil(data.total / SIZE));
  const filter = (setter) => (e) => { setter(e.target.value); setPage(1); };

  return (
    <div>
      <h1>Alerts <span className="live-pill">● LIVE</span></h1>
      <div className="row filters">
        <select value={severity} onChange={filter(setSeverity)} aria-label="Severity">
          <option value="">All severities</option>
          {["emergency", "security", "safety", "info"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={status} onChange={filter(setStatus)} aria-label="Status">
          <option value="">All statuses</option>
          {["new", "notified", "escalated", "acknowledged", "resolved", "false_alarm"].map((s) => <option key={s} value={s}>{human(s)}</option>)}
        </select>
        <select value={type} onChange={filter(setType)} aria-label="Type">
          <option value="">All types</option>
          {TYPES.map((t) => <option key={t} value={t}>{human(t)}</option>)}
        </select>
        <span className="muted">{data.total} alerts</span>
      </div>
      <div className="card table-wrap">
        <table className="alerts-table">
          <thead>
            <tr>
              <th>When</th><th>House · room</th><th>Type</th><th>Severity</th><th>Status</th><th>Conf.</th><th>Count</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((a) => (
              <tr key={a.alertId} className={`clickable ${fresh.has(a.alertId) ? "flash" : ""}`} onClick={() => nav(`/alerts/${a.alertId}`)}>
                <td>{fmtTime(a.createdAt)}</td>
                <td>{a.houseName} · {a.roomName}</td>
                <td><Link to={`/alerts/${a.alertId}`}><strong>{human(a.type)}</strong></Link></td>
                <td><Chip value={a.severity} /></td>
                <td><Chip value={a.status} />{a.escalateRemainingSec != null && <span className="muted"> ⏱ {a.escalateRemainingSec}s</span>}</td>
                <td>{pct(a.confidence)}</td>
                <td>{a.count > 1 ? <strong>×{a.count}</strong> : a.count}</td>
              </tr>
            ))}
            {!data.items.length && (
              <tr><td colSpan={7} className="muted">No alerts yet. Staff can send events from Operations.</td></tr>
            )}
          </tbody>
        </table>
        <div className="row pager">
          <button className="btn ghost small" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Prev</button>
          <span className="muted">Page {page} of {pages}</span>
          <button className="btn ghost small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
        </div>
      </div>
    </div>
  );
}
