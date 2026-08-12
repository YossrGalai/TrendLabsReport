import { Download, FileSpreadsheet } from "lucide-react";
import { useReportHistory } from "../../../hooks/useReports";
import { downloadReport } from "../../../api/reports";

const MONTH_NAMES = Array.from({ length: 12 }, (_, i) =>
  new Date(2000, i).toLocaleString("fr-FR", { month: "long" })
);

const statusStyles = {
  DONE: "bg-success/10 text-success",
  RUNNING: "bg-warning/15 text-warning-foreground",
  ERROR: "bg-destructive/10 text-destructive",
};
const statusLabels = { DONE: "Terminé", RUNNING: "En cours", ERROR: "Échec" };

export function ReportHistory({ trelloBoardId, projectLabelId, projects }) {
  const { data: reports, isLoading, error } = useReportHistory(trelloBoardId, projectLabelId);
  const projectName = (id) => projects?.find((p) => p.id === id)?.name ?? `Projet #${id}`;

  if (!trelloBoardId) return null;

  return (
    <section className="mt-10">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-bold tracking-tight">Historique des rapports</h2>
        <p className="text-sm text-muted-foreground">
          {isLoading ? "…" : `${reports?.length ?? 0} rapport${(reports?.length ?? 0) > 1 ? "s" : ""}`}
        </p>
      </div>

      {error && <p className="text-sm text-destructive">Impossible de charger l'historique.</p>}

      {!error && (
        <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-[var(--shadow-card)]">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/50 text-left">
                  <th className="label-caps px-6 py-3">Période</th>
                  {!projectLabelId && <th className="label-caps px-6 py-3">Projet</th>}
                  <th className="label-caps px-6 py-3">Sprints</th>
                  <th className="label-caps px-6 py-3">Statut</th>
                  <th className="label-caps px-6 py-3">Généré le</th>
                  <th className="px-6 py-3" />
                </tr>
              </thead>
              <tbody>
                {isLoading && (
                  <tr><td colSpan={6} className="px-6 py-6 text-center text-muted-foreground">Chargement…</td></tr>
                )}
                {!isLoading && (reports?.length ?? 0) === 0 && (
                  <tr><td colSpan={6} className="px-6 py-6 text-center text-muted-foreground">Aucun rapport généré pour l'instant.</td></tr>
                )}
                {reports?.map((r) => {
                  const statusKey = r.status?.toUpperCase();
                  return (
                    <tr key={r.id} className="border-b border-border last:border-0 hover:bg-muted/40">
                      <td className="px-6 py-4 font-medium">
                        <span className="inline-flex items-center gap-2">
                          <FileSpreadsheet className="size-4 text-muted-foreground" />
                          {MONTH_NAMES[r.report_month - 1]} {r.report_year}
                        </span>
                      </td>
                      {!projectLabelId && (
                        <td className="px-6 py-4 text-muted-foreground">{projectName(r.project_label_id)}</td>
                      )}
                      <td className="px-6 py-4 font-mono text-xs text-muted-foreground">{r.sprint_numbers.join(", ")}</td>
                      <td className="px-6 py-4">
                        {statusStyles[statusKey] ? (
                          <span className={"inline-flex rounded-full px-2.5 py-1 text-xs font-semibold " + statusStyles[statusKey]}>
                            {statusLabels[statusKey]}
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground">{r.status ?? "—"}</span>
                        )}
                      </td>
                      <td className="px-6 py-4 font-mono text-xs text-muted-foreground">
                        {r.generated_at
                          ? new Date(r.generated_at).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })
                          : "—"}
                      </td>
                      <td className="px-6 py-4 text-right">
                        {statusKey === "DONE" ? (
                          <button
                            type="button"
                            onClick={() => downloadReport(r.id)}
                            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-primary hover:bg-primary/10"
                          >
                            <Download className="size-4" />
                            Télécharger
                          </button>
                        ) : (
                          <span className="text-xs text-muted-foreground">{r.error_message ? "Voir l'erreur" : "—"}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}