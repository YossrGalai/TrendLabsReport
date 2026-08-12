import { apiFetch } from "./http";

const BASE_URL = import.meta.env.VITE_API_URL;

export async function fetchBoards() {
  const res = await apiFetch(`${BASE_URL}/api/sync/debug/boards`);
  if (!res.ok) throw new Error("Impossible de charger les boards");
  const data = await res.json();
  return data.boards;
}

export async function fetchProjects(trelloBoardId) {
  const res = await apiFetch(`${BASE_URL}/api/boards/${trelloBoardId}/projects`);
  if (!res.ok) throw new Error("Impossible de charger les projets");
  return res.json();
}

// Sprints trouvés pour ce board+projet sur le mois choisi — alimente la pré-sélection de
// SprintNumbersInput dans le formulaire mensuel (même concept que le rapport annuel).
export async function fetchSprintsInMonth(trelloBoardId, projectLabelId, month, year) {
  const url = new URL(`${BASE_URL}/api/boards/${trelloBoardId}/sprints`);
  url.searchParams.set("project_label_id", String(projectLabelId));
  url.searchParams.set("month", String(month));
  url.searchParams.set("year", String(year));
  const res = await apiFetch(url);
  if (!res.ok) throw new Error("Impossible de charger les sprints du mois");
  const data = await res.json();
  return data.sprint_numbers;
}

export async function generateReport(trelloBoardId, payload) {
  const res = await apiFetch(`${BASE_URL}/api/reports/generate/${trelloBoardId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Erreur serveur (${res.status})`);
  return res.json();
}

export async function downloadReport(reportRunId) {
  const res = await apiFetch(`${BASE_URL}/api/reports/download/${reportRunId}`);
  if (!res.ok) throw new Error("Fichier non disponible");
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition");
  const filename = disposition?.match(/filename="?(.+?)"?$/)?.[1] ?? "rapport.xlsx";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function fetchReportHistory(trelloBoardId, projectLabelId) {
  const url = new URL(`${BASE_URL}/api/reports/${trelloBoardId}`);
  if (projectLabelId) url.searchParams.set("project_label_id", String(projectLabelId));
  const res = await apiFetch(url);
  if (!res.ok) throw new Error("Impossible de charger l'historique");
  return res.json();
}

export async function syncBoard(trelloBoardId) {
  const res = await apiFetch(`${BASE_URL}/api/sync/full/${trelloBoardId}`, { method: "POST" });
  // Le backend peut renvoyer HTTP 200 avec { success: false, error: "..." } dans le corps
  // (cas d'une exception interne attrapée côté service) : on ne peut donc pas se fier
  // uniquement à res.ok pour savoir si la synchronisation a réellement réussi.
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data?.error || data?.detail || "Échec de la synchronisation");
  }
  if (data && data.success === false) {
    throw new Error(data.error || "Échec de la synchronisation");
  }
  return data;
}

export async function fetchTrelloBoards() {
  const res = await apiFetch(`${BASE_URL}/trello/boards`);
  if (!res.ok) throw new Error("Impossible de charger les boards Trello");
  const data = await res.json();
  return data.boards;
}

// Compteurs + dernière synchro d'un board (lists, members, labels, cards, card_history)
export async function fetchSyncStatus(trelloBoardId) {
  const res = await apiFetch(`${BASE_URL}/api/sync/status/${trelloBoardId}`);
  if (!res.ok) throw new Error("Impossible de charger le statut de synchronisation");
  return res.json();
}