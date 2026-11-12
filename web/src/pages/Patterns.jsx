import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, onLive } from "../api";

export default function Patterns({ houseId }) {
  const [data, setData] = useState(null);
  const [msg, setMsg] = useState("");
  const nav = useNavigate();

  const load = () => api.patterns(houseId).then(setData);
  useEffect(() => {
    load();
    return onLive((m) => { if (m.event === "event" || m.event === "alert") load(); });
  }, [houseId]);

  async function run() {
    setMsg("");
    const r = await api.runPatterns(houseId);
    if (r.created?.length) nav(`/alerts/${r.created[0].alertId}`);
    else setMsg("Pattern job ran: routine looks normal (learn only).");
  }

  if (!data) return <p>Loading…</p>;
  const max = Math.max(1, ...data.today, ...data.baseline);
  const H = 160;
  return (
    <div>
      <h1>Activity patterns</h1>
      <div className="row">
        <div className="card stat"><div className="muted">Deviation today</div><div className={`big ${data.deviationScore > 0.8 ? "error" : ""}`}>{data.deviationScore.toFixed(2)}</div></div>
        {data.recentScores.slice(0, 2).map((s) => (
          <div className="card stat" key={s.day}><div className="muted">{s.day}</div><div className={`big ${s.score > 0.8 ? "error" : ""}`}>{s.score.toFixed(2)}</div></div>
        ))}
        <div className="card stat"><div className="muted">Baseline days</div><div className="big">{data.baselineDays}</div></div>
        <button className="btn big-btn" onClick={run}>Run pattern job</button>
      </div>
      {msg && <p className="oktext">{msg}</p>}
      <div className="card">
        <h2>Today ({data.day}) vs 14-day baseline, by hour</h2>
        <svg viewBox={`0 0 ${24 * 30} ${H + 24}`} className="chart" role="img" aria-label="Hourly activity chart">
          {data.today.map((v, i) => {
            const b = data.baseline[i];
            const bh = (b / max) * H;
            const th = (v / max) * H;
            return (
              <g key={i} transform={`translate(${i * 30 + 3},0)`}>
                <rect x="0" y={H - bh} width="11" height={bh} fill="#90caf9"><title>{`${i}:00 baseline ${b.toFixed(1)}`}</title></rect>
                <rect x="12" y={H - th} width="11" height={th} fill={i > data.localHour ? "#e0e0e0" : "#d32f2f"}><title>{`${i}:00 today ${v}`}</title></rect>
                <text x="12" y={H + 16} fontSize="11" textAnchor="middle" fill="#5b677a">{i}</text>
              </g>
            );
          })}
          <line x1={(data.localHour + 1) * 30} x2={(data.localHour + 1) * 30} y1="0" y2={H} stroke="#0b1f3a" strokeDasharray="4 3" />
        </svg>
        <p className="muted"><span className="swatch" style={{ background: "#90caf9" }} /> baseline average · <span className="swatch" style={{ background: "#d32f2f" }} /> today (dashed line = now). Every sound event adds to today’s profile.</p>
        <p className="muted small-print">Rules (Table 46): needs ≥ 7 baseline days · no activity by noon → safety alert · deviation &gt; 0.8 two days in a row → “routine changed” safety alert · otherwise learn only.</p>
      </div>
    </div>
  );
}
