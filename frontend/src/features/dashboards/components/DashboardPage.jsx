import { useQueries } from "@tanstack/react-query";
import { Layers, FileSpreadsheet, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { useBoards } from "../../../hooks/useReports";
import { fetchReportHistory } from "../../../api/reports";

export function DashboardPage() {
  const { data: boards, isLoading: loadingBoards } = useBoards();

  // Pas d'endpoint "stats globales" côté backend : on agrège nous-mêmes en interrogeant
  // l'historique de chaque board synchronisé (GET /api/reports/{trello_board_id}).
  const reportQueries = useQueries({
    queries: (boards ?? []).map((b) => ({
      queryKey: ["reportHistory", b.trello_id, null],
      queryFn: () => fetchReportHistory(b.trello_id),
    })),
  });

  const loadingReports = boards === undefined || reportQueries.some((q) => q.isLoading);
  const allReports = reportQueries.flatMap((q) => q.data ?? []);

  const totalReports = allReports.length;
  const doneReports = allReports.filter((r) => r.status?.toUpperCase() === "DONE").length;
  const errorReports = allReports.filter((r) => r.status?.toUpperCase() === "ERROR").length;
  const activeBoards = (boards ?? []).filter((b) => b.last_sync_at).length;

  const isLoading = loadingBoards || loadingReports;

  const stats = [
    { label: "Boards synchronisés", value: boards?.length ?? 0, icon: Layers },
    { label: "Boards actifs", value: activeBoards, icon: Layers },
    { label: "Rapports générés", value: totalReports, icon: FileSpreadsheet },
    { label: "Rapports réussis", value: doneReports, icon: CheckCircle2 },
    { label: "Échecs", value: errorReports, icon: XCircle },
  ];

  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-1">TrendLabs</p>
      <h1 className="text-3xl font-display font-semibold text-foreground">Tableau de bord</h1>
      <p className="text-sm text-muted-foreground mt-1 mb-6">
        Vue d'ensemble de l'activité de reporting, tous boards confondus.
      </p>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {stats.map(({ label, value, icon: Icon }) => (
          <div key={label} className="bg-card border border-border rounded-2xl shadow-[var(--shadow-card)] p-6">
            <div className="flex items-center gap-2 text-muted-foreground mb-2">
              <Icon size={16} />
              <p className="label-caps">{label}</p>
            </div>
            <p className="text-3xl font-display font-semibold text-foreground">
              {isLoading ? <Loader2 size={22} className="animate-spin text-muted-foreground" /> : value}
            </p>
          </div>
        ))}
      </div>

      {!isLoading && (boards?.length ?? 0) === 0 && (
        <p className="mt-6 text-sm text-muted-foreground">
          Aucun board synchronisé pour l'instant — les statistiques apparaîtront dès qu'un board sera synchronisé.
        </p>
      )}
    </>
  );
}