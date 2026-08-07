const BASE_URL = import.meta.env.VITE_API_URL;

// Sprints couvrant la période choisie — alimente la checklist du formulaire, pré-cochée par
// défaut (l'admin peut décocher avant de générer). board_ids est répété en query param
// (?board_ids=1&board_ids=2), convention FastAPI pour une liste en paramètre de requête GET.
export async function fetchAnnualSprints(dateStart, dateEnd, boardIds) {
  const url = new URL(`${BASE_URL}/api/reports/annual/sprints`);
  url.searchParams.set("date_start", dateStart);
  url.searchParams.set("date_end", dateEnd);
  (boardIds || []).forEach((id) => url.searchParams.append("board_ids", String(id)));
  const res = await fetch(url);
  if (!res.ok) throw new Error("Impossible de charger les sprints de la période");
  const data = await res.json();
  return data.sprint_numbers;
}

// Contrairement à generateReport (mensuel, synchrone), la génération annuelle tourne en
// tâche de fond côté backend et répond 202 immédiatement avec status="pending" — le front
// doit ensuite poller fetchAnnualReportStatus (cf. useAnnualReportStatus).
export async function generateAnnualReport(payload) {
  const res = await fetch(`${BASE_URL}/api/reports/annual/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data?.detail || `Erreur serveur (${res.status})`);
  }
  return data;
}

export async function fetchAnnualReportStatus(reportRunId) {
  const res = await fetch(`${BASE_URL}/api/reports/annual/${reportRunId}`);
  if (!res.ok) throw new Error("Impossible de récupérer le statut du rapport");
  return res.json();
}

export async function downloadAnnualReport(reportRunId) {
  const res = await fetch(`${BASE_URL}/api/reports/annual/${reportRunId}/download`);
  if (!res.ok) throw new Error("Fichier non disponible");
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition");
  const filename = disposition?.match(/filename="?(.+?)"?$/)?.[1] ?? "rapport_annuel.xlsx";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}