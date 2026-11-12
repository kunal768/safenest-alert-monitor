const TOKEN_KEY = "safenest-token";
const USER_KEY = "safenest-user";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function getUser() {
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export function setSession(user) {
  localStorage.setItem(TOKEN_KEY, user.token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

const linkQs = (link) => (link ? `?link=${encodeURIComponent(link)}` : "");

async function req(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const token = getToken();
  if (token && !path.includes("link=")) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.message || res.statusText);
    err.body = data;
    err.status = res.status;
    throw err;
  }
  return data;
}

export const api = {
  login: (email) => req("/auth/login", { method: "POST", body: JSON.stringify({ email }) }),
  users: () => req("/auth/users"),
  health: () => req("/health"),
  features: () => req("/config/features"),
  alerts: (q = "") => req("/alerts" + q),
  // `link` = care-center one-alert link token (no login).
  alert: (id, link) => req(`/alerts/${id}${linkQs(link)}`),
  notifications: (id, link) => req(`/alerts/${id}/notifications${linkQs(link)}`),
  patchAlert: (id, body, link) => req(`/alerts/${id}${linkQs(link)}`, { method: "PATCH", body: JSON.stringify(body) }),
  escalate: (id, version, target = "family", note) =>
    req(`/alerts/${id}/escalate`, { method: "POST", body: JSON.stringify({ target, version, note }) }),
  openAlerts: (houseIds) => req(`/alerts/open?houseIds=${houseIds}`),
  rules: (houseId) => req(`/houses/${houseId}/alert-rules`),
  saveRules: (houseId, body) =>
    req(`/houses/${houseId}/alert-rules`, { method: "PUT", body: JSON.stringify(body) }),
  patterns: (houseId) => req(`/houses/${houseId}/patterns`),
  runPatterns: (houseId) => req(`/patterns/run?houseId=${houseId}`, { method: "POST" }),
  missed: (id) => req(`/devices/${id}/missed`, { method: "POST" }),
  models: () => req("/ai/models"),
  registerModel: (tag, name) => req("/ai/models", { method: "POST", body: JSON.stringify({ tag, name }) }),
  ollamaTags: () => req("/ai/ollama/tags"),
  syncModels: () => req("/ai/models/sync", { method: "POST" }),
  evaluate: (id) => req(`/ai/models/${id}/evaluate`, { method: "POST" }),
  deploy: (id, body) => req(`/ai/models/${id}/deploy`, { method: "POST", body: JSON.stringify(body) }),
  houses: () => req("/houses"),
  audit: (limit = 15) => req(`/audit?limit=${limit}`),
  scenario: (name) => req(`/sim/scenario?name=${name}`, { method: "POST" }),
  custom: (body) => req("/sim/custom", { method: "POST", body: JSON.stringify(body) }),
  coughs: () => req("/sim/coughs?count=10", { method: "POST" }),
  duplicate: () => req("/sim/duplicate", { method: "POST" }),
  outage: (on) => req("/sim/outage", { method: "POST", body: JSON.stringify({ on }) }),
  event: (body) => req("/events", { method: "POST", body: JSON.stringify(body) }),
};

/** Subscribe to WebSocket pushes relayed by App. Returns an unsubscribe function. */
export function onLive(fn) {
  const handler = (e) => fn(e.detail || {});
  window.addEventListener("safenest-live", handler);
  return () => window.removeEventListener("safenest-live", handler);
}

export const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
export const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");
