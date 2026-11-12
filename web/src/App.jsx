import React, { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, clearSession, getUser, onLive, setSession } from "./api";
import Alerts from "./pages/Alerts.jsx";
import AlertDetail from "./pages/AlertDetail.jsx";
import Rules from "./pages/Rules.jsx";
import Models from "./pages/Models.jsx";
import Patterns from "./pages/Patterns.jsx";
import Operations from "./pages/Operations.jsx";

function StatusBar() {
  const [health, setHealth] = useState(null);
  const load = () => api.health().then(setHealth).catch(() => setHealth({ status: "down" }));
  useEffect(() => {
    load();
    const off = onLive((m) => { if (m.event === "model") load(); });
    const t = setInterval(load, 10000);
    return () => { off(); clearInterval(t); };
  }, []);
  if (!health) return null;
  const ok = health.status === "ok" && !health.aiOutage;
  return (
    <span className="status" data-testid="statusbar">
      <span className={`dot ${ok ? "up" : "down"}`} />
      AI: <strong>{health.activeModel || "—"}</strong> · {health.adapter}
      {health.aiOutage && <strong className="warn-text"> · OUTAGE</strong>}
      {health.adapter !== "mock" && !health.ollama && <strong className="warn-text"> · Ollama down</strong>}
    </span>
  );
}

/** Care-center view: an escalation message links here; it opens this one alert only, no login. */
function LinkView() {
  const loc = useLocation();
  const token = new URLSearchParams(loc.search).get("t");
  return (
    <>
      <header className="topbar"><div className="brand">SAFENEST · Care center alert link</div></header>
      <main className="content narrow">
        <Routes>
          <Route path="/link/:id" element={<AlertDetail linkToken={token} />} />
        </Routes>
      </main>
    </>
  );
}

export default function App() {
  const [user, setUser] = useState(getUser());
  const nav = useNavigate();
  const loc = useLocation();

  useEffect(() => {
    if (!user) return;
    let ws;
    let stop = false;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/live`);
      ws.onmessage = (e) => {
        let detail = {};
        try { detail = JSON.parse(e.data); } catch { /* ignore */ }
        window.dispatchEvent(new CustomEvent("safenest-live", { detail }));
      };
      ws.onclose = () => { if (!stop) setTimeout(connect, 1500); };
    };
    connect();
    return () => { stop = true; ws?.close(); };
  }, [user]);

  async function login(email) {
    const u = await api.login(email);
    setSession(u);
    setUser(u);
    nav("/alerts");
  }

  if (loc.pathname.startsWith("/link/")) return <LinkView />;

  if (!user) {
    return (
      <div className="card login">
        <div className="brand-lg">SAFENEST</div>
        <h1>Alert Tracking &amp; Monitoring</h1>
        <p className="muted">Demo sign-in with seeded users.</p>
        <div className="col">
          <button className="btn big-btn" onClick={() => login("staff@safenest.local")}>Sign in as Staff (Jamie)</button>
          <button className="btn ghost big-btn" onClick={() => login("owner@safenest.local")}>Sign in as Owner (Ana Lopez)</button>
        </div>
      </div>
    );
  }

  const houseId = user.houseIds?.[0] || "H-1042";
  return (
    <>
      <header className="topbar">
        <div className="brand">SAFENEST · Alert Tracking</div>
        <StatusBar />
        <div className="topbar-right">
          <label className="role-switch">
            Role
            <select value={user.email} onChange={(e) => login(e.target.value)} aria-label="Switch role">
              <option value="staff@safenest.local">Staff · Jamie</option>
              <option value="owner@safenest.local">Owner · Ana Lopez</option>
            </select>
          </label>
          <button className="btn ghost small" onClick={() => { clearSession(); setUser(null); }}>Sign out</button>
        </div>
      </header>
      <div className="shell">
        <nav className="nav">
          <NavLink to="/alerts">Alerts</NavLink>
          <NavLink to="/rules">House rules</NavLink>
          <NavLink to="/models">AI models</NavLink>
          <NavLink to="/patterns">Patterns</NavLink>
          {user.role === "staff" && <NavLink to="/ops">Operations</NavLink>}
        </nav>
        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/alerts" replace />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/alerts/:id" element={<AlertDetail />} />
            <Route path="/rules" element={<Rules houseId={houseId} />} />
            <Route path="/models" element={<Models staff={user.role === "staff"} />} />
            <Route path="/patterns" element={<Patterns houseId={houseId} />} />
            <Route path="/ops" element={user.role === "staff" ? <Operations /> : <Navigate to="/alerts" replace />} />
          </Routes>
        </main>
      </div>
    </>
  );
}
