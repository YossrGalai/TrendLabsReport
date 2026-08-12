import { apiFetch, setToken, clearToken } from "./http";

const BASE_URL = import.meta.env.VITE_API_URL;

// PAS apiFetch ici : au moment du login, il n'y a justement pas encore de token à joindre.
export async function login(email, password) {
  const res = await fetch(`${BASE_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data?.detail || "Email ou mot de passe incorrect");
  }
  setToken(data.access_token);
  return data;
}

// apiFetch ici, car protégé — sert à vérifier au chargement de l'app si un token déjà
// stocké (localStorage, d'une session précédente) est toujours valide.
export async function fetchMe() {
  const res = await apiFetch(`${BASE_URL}/api/auth/me`);
  if (!res.ok) {
    clearToken();
    throw new Error("Session expirée");
  }
  return res.json();
}

export function logout() {
  clearToken();
}