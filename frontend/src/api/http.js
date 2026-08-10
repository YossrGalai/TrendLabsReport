// Stockage du token + wrapper fetch centralisé — TOUT appel vers le backend doit passer par
// apiFetch (pas fetch() directement) pour que le header Authorization soit ajouté
// automatiquement et que l'expiration/invalidité du token redirige proprement vers /login.

const TOKEN_KEY = "trendlabs_auth_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

// Enregistré par AuthContext au montage — évite un import circulaire entre http.js et
// AuthContext.jsx (http.js ne connaît pas React, juste "quoi faire" en cas de 401/403).
let _unauthorizedHandler = null;
export function setUnauthorizedHandler(handler) {
  _unauthorizedHandler = handler;
}

// Signature identique à fetch() : accepte une string OU un objet URL (reports.js utilise
// les deux selon les endpoints), donc aucun appel existant n'a besoin d'être restructuré —
// juste renommé fetch(...) -> apiFetch(...).
export async function apiFetch(input, init = {}) {
  const token = getToken();
  const headers = new Headers(init.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(input, { ...init, headers });

  if ((res.status === 401 || res.status === 403) && _unauthorizedHandler) {
    _unauthorizedHandler();
  }
  return res;
}