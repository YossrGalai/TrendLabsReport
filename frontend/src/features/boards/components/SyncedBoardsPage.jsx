import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { useBoards, useSyncBoard, useSyncStatus } from "../../../hooks/useReports";
import { SyncNewBoardPanel } from "../../reports/components/SyncNewBoardPanel";

function BoardStatusDetails({ trelloId }) {
  const { data: status, isLoading } = useSyncStatus(trelloId);

  if (isLoading) return <p className="px-4 pb-4 text-sm text-muted-foreground">Chargement du détail…</p>;
  if (!status) return null;

  const rows = [
    ["Listes", status.counts?.lists],
    ["Membres", status.counts?.members],
    ["Labels", status.counts?.labels],
    ["Cartes", status.counts?.cards],
    ["Historique de cartes", status.counts?.card_history],
  ];

  return (
    <div className="grid grid-cols-2 gap-3 px-4 pb-4 sm:grid-cols-5">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded-lg bg-muted/50 px-3 py-2">
          <p className="label-caps">{label}</p>
          <p className="font-mono text-sm font-semibold text-foreground">{value ?? "—"}</p>
        </div>
      ))}
    </div>
  );
}

export function SyncedBoardsPage() {
  const { data: boards, isLoading, error } = useBoards();
  const syncMutation = useSyncBoard();
  const queryClient = useQueryClient();
  const [syncingId, setSyncingId] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  const handleResync = async (trelloId) => {
    setSyncingId(trelloId);
    try {
      await syncMutation.mutateAsync(trelloId);
      await queryClient.invalidateQueries({ queryKey: ["boards"] });
      await queryClient.invalidateQueries({ queryKey: ["syncStatus", trelloId] });
    } finally {
      setSyncingId(null);
    }
  };

  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-1">TrendLabs</p>
      <h1 className="text-3xl font-display font-semibold text-foreground">Boards synchronisés</h1>
      <p className="text-sm text-muted-foreground mt-1 mb-6">
        Boards Trello disponibles pour la génération de rapports, avec leur état de synchronisation.
      </p>

      <div className="bg-card border border-border rounded-2xl shadow-[var(--shadow-card)] p-6 mb-6">
        <SyncNewBoardPanel
          syncedBoards={boards}
          onSynced={() => queryClient.invalidateQueries({ queryKey: ["boards"] })}
        />
      </div>

      {error && <p className="text-sm text-destructive">Impossible de charger les boards.</p>}

      <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-[var(--shadow-card)]">
        {isLoading && <p className="px-6 py-6 text-sm text-muted-foreground">Chargement…</p>}
        {!isLoading && (boards?.length ?? 0) === 0 && (
          <p className="px-6 py-6 text-sm text-muted-foreground">Aucun board synchronisé pour l'instant.</p>
        )}
        {boards?.map((b) => {
          const isExpanded = expandedId === b.trello_id;
          return (
            <div key={b.trello_id} className="border-b border-border last:border-0">
              <div className="flex items-center justify-between px-4 py-3.5">
                <button
                  type="button"
                  onClick={() => setExpandedId(isExpanded ? null : b.trello_id)}
                  className="flex items-center gap-2 text-sm font-medium text-foreground hover:text-primary"
                >
                  {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  {b.name}
                </button>
                <div className="flex items-center gap-4">
                  <span className="text-xs text-muted-foreground font-mono">
                    {b.last_sync_at
                      ? `Dernière synchro : ${new Date(b.last_sync_at).toLocaleString("fr-FR")}`
                      : "Jamais synchronisé"}
                  </span>
                  <button
                    type="button"
                    onClick={() => handleResync(b.trello_id)}
                    disabled={syncingId === b.trello_id}
                    className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:text-primary hover:border-primary disabled:opacity-50"
                  >
                    {syncingId === b.trello_id ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : (
                      <RefreshCw size={12} />
                    )}
                    Resynchroniser
                  </button>
                </div>
              </div>
              {isExpanded && <BoardStatusDetails trelloId={b.trello_id} />}
            </div>
          );
        })}
      </div>
    </>
  );
}