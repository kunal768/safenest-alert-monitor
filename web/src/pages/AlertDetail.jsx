import React, { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtTime, onLive, pct } from "../api";
import { Chip, human } from "./Alerts.jsx";

/** Draws the real waveform of the alert's clip (peak per pixel column). */
function Waveform({ src }) {
  const canvas = useRef(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!src || !canvas.current) return;
    let cancelled = false;
    (async () => {
      try {
        const buf = await (await fetch(src)).arrayBuffer();
        const Ctx = window.AudioContext || window.webkitAudioContext;
        const ctx = new Ctx();
        const audio = await ctx.decodeAudioData(buf);
        ctx.close();
        if (cancelled) return;
        const data = audio.getChannelData(0);
        const c = canvas.current;
        const g = c.getContext("2d");
        const { width: w, height: h } = c;
        g.fillStyle = "#0b1f3a";
        g.fillRect(0, 0, w, h);
        g.fillStyle = "#2aa3ff";
        const step = Math.max(1, Math.floor(data.length / w));
        for (let x = 0; x < w; x++) {
          let peak = 0;
          for (let i = x * step; i < (x + 1) * step && i < data.length; i++) peak = Math.max(peak, Math.abs(data[i]));
          const bar = Math.max(1, peak * h * 0.9);
          g.fillRect(x, (h - bar) / 2, 1, bar);
        }
      } catch {
        setErr("waveform unavailable");
      }
    })();
    return () => { cancelled = true; };
  }, [src]);
  return (
    <>
      <canvas ref={canvas} className="wave" width="640" height="72" />
      {err && <span className="muted">{err}</span>}
    </>
  );
}

function Countdown({ seconds, next }) {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    setLeft(seconds);
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [seconds]);
  return (
    <div className="countdown" data-testid="countdown">
      No acknowledgement — escalating{next ? <> to <strong>{next}</strong></> : null} in <strong className="big">{left}s</strong>
    </div>
  );
}

export default function AlertDetail({ linkToken }) {
  const { id } = useParams();
  const [alert, setAlert] = useState(null);
  const [notes, setNotes] = useState([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [target, setTarget] = useState("family");
  const linkMode = !!linkToken;

  async function load() {
    try {
      const [a, n] = await Promise.all([api.alert(id, linkToken), api.notifications(id, linkToken)]);
      setAlert(a);
      setNotes(n);
    } catch (e) {
      setErr(e.body?.message || e.message);
    }
  }

  useEffect(() => {
    load();
    const off = onLive((m) => { if (m.event === "alert" && m.payload?.alertId === id) load(); });
    return off;
  }, [id]);

  async function act(action) {
    setErr("");
    setBusy(true);
    try {
      if (action === "escalate") await api.escalate(id, alert.version, target, note || undefined);
      else await api.patchAlert(id, { action, version: alert.version, note: note || undefined }, linkToken);
      setNote("");
      await load();
    } catch (e) {
      setErr(e.body?.message || e.message);
      await load();
    }
    setBusy(false);
  }

  if (!alert) return <p>{err || "Loading…"}</p>;
  const s = alert.status;
  const closed = ["resolved", "false_alarm"].includes(s);
  const can = {
    acknowledge: ["new", "notified", "escalated"].includes(s),
    resolve: !linkMode && s === "acknowledged",
    falseAlarm: !linkMode && ["notified", "escalated", "acknowledged"].includes(s),
    escalate: !linkMode && ["new", "notified", "acknowledged"].includes(s),
  };
  const ev = alert.event;

  return (
    <div>
      {linkMode ? (
        <p className="link-banner">You opened this alert from an escalation message. This link shows only this alert and can acknowledge it.</p>
      ) : (
        <p><Link to="/alerts" className="muted">← All alerts</Link></p>
      )}
      <div className={`alert-header sev-${alert.severity}`}>
        <div>
          <h1>{human(alert.type)} · {alert.roomName}</h1>
          <div className="muted">{alert.houseName} · {alert.alertId} · {fmtTime(alert.createdAt)}</div>
        </div>
        <div className="row">
          <Chip value={alert.severity} />
          <Chip value={alert.status} />
          {alert.count > 1 && <span className="chip count">×{alert.count} events</span>}
        </div>
      </div>

      <div className="detail-grid">
        <div className="card">
          <h2>Event</h2>
          {ev ? (
            <>
              <p className="quote">“{ev.description}”</p>
              <p className="muted">{ev.levelDb} dB · {ev.durationSec}s · device {ev.deviceId} · event {ev.eventId}</p>
            </>
          ) : (
            <p className="muted">Raised by the system (no sound event).</p>
          )}
          <div className="confidence">
            <span>Confidence</span>
            <div className="bar"><div style={{ width: pct(alert.confidence) }} /></div>
            <strong>{pct(alert.confidence)}</strong>
          </div>

          <h2>AI explanation</h2>
          <div className="explain" data-testid="ai-explanation">
            <p>{alert.reason}</p>
            {alert.modelVersion && (
              <p className="muted">
                Model <strong>{alert.modelVersion}</strong> · prompt <strong>{alert.promptVersion}</strong> · {alert.latencyMs ?? "—"} ms
              </p>
            )}
            {ev?.status === "unclassified" && <p className="warn-text">AI could not classify this event — warning raised because it was loud (fail-loud).</p>}
            <p className="muted small-print">LLM confidence is not calibrated; house rules apply the thresholds.</p>
          </div>

          {alert.clipToken && (
            <>
              <h2>Clip</h2>
              <Waveform src={`/alerts/${alert.alertId}/clip?token=${alert.clipToken}`} />
              <audio controls src={`/alerts/${alert.alertId}/clip?token=${alert.clipToken}`} />
              <p className="muted small-print">Signed link, expires in 5 minutes.</p>
            </>
          )}
        </div>

        <div className="card">
          <h2>Response</h2>
          {!closed && (
            <input className="note" aria-label="Note" placeholder="Add a note (optional), e.g. Calling her now" value={note} onChange={(e) => setNote(e.target.value)} />
          )}
          <div className="row actions">
            <button className="btn big-btn" disabled={busy || !can.acknowledge} onClick={() => act("acknowledge")}>Acknowledge</button>
            {!linkMode && <button className="btn ok big-btn" disabled={busy || !can.resolve} onClick={() => act("resolve")}>Resolve</button>}
            {!linkMode && <button className="btn ghost big-btn" disabled={busy || !can.falseAlarm} onClick={() => act("falseAlarm")}>False alarm</button>}
          </div>
          {!linkMode && (
            <div className="row escalate-row">
              <select aria-label="Escalation target" value={target} onChange={(e) => setTarget(e.target.value)} disabled={!can.escalate}>
                <option value="family">Next family contact</option>
                <option value="careCenter">Care center</option>
                <option value="emergency">Care center + emergency services</option>
              </select>
              <button className="btn danger big-btn" disabled={busy || !can.escalate} onClick={() => act("escalate")}>Escalate</button>
            </div>
          )}
          {closed && <p className="muted">Closed alerts are read-only.</p>}
          {alert.ackBy && <p className="muted">Acknowledged by {alert.ackBy}</p>}
          {alert.escalateRemainingSec != null && <Countdown seconds={alert.escalateRemainingSec} next={alert.nextContact} />}
          {err && <p className="error">{err}</p>}

          <h2>Notification timeline</h2>
          <div className="timeline" data-testid="timeline">
            {notes.map((n) => (
              <div className={`item ${n.status}`} key={n.id}>
                <strong>{n.channel.toUpperCase()}</strong> → {n.contact}{n.priority ? ` (priority ${n.priority})` : ""} · <span className={n.status === "failed" ? "error" : "oktext"}>{n.status}</span>
                <div className="muted">{fmtTime(n.sentAt)} · {n.detail}</div>
                {n.link && <a className="small-print" href={n.link} target="_blank" rel="noreferrer" data-testid="care-link">Open the care-center link (this alert only) ↗</a>}
              </div>
            ))}
            {!notes.length && <p className="muted">No notifications yet.</p>}
          </div>

          <h2>History</h2>
          <ul className="history">
            {(alert.history || []).map((h, i) => (
              <li key={i}><span className="muted">{fmtTime(h.ts)}</span> <strong>{human(h.action)}</strong> {h.detail} <span className="muted">· {h.actor}</span></li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
