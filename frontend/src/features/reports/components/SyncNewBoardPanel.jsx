import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, X, Loader2, RefreshCw } from "lucide-react";
import { useTrelloBoards, useSyncBoard } from "../../../hooks/useReports";

export function SyncNewBoardPanel({ syncedBoards, onSynced }) {
  const [open, setOpen] = useState(false);
  const [syncingId, setSyncingId] = useState(null);
  const [error, setError] = useState(null);

  const { data: trelloBoards, isLoading } = useTrelloBoards(open);
  const syncMutation = useSyncBoard();
  const queryClient = useQueryClient();

  const syncedIds = new Set((syncedBoards ?? []).map((b) => b.trello_id));
  const unsyncedBoards = (trelloBoards ?? []).filter((b) => !syncedIds.has(b.id));

  const handleSync = async (board) => {
    setSyncingId(board.id);
    setError(null);
    try {
      await syncMutation.mutateAsync(board.id);
      await queryClient.invalidateQueries({ queryKey: ["boards"] });
      onSynced(board.id);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Échec de la synchronisation");
    } finally {
      setSyncingId(null);
    }
  };

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="text-sm text-primary hover:text-primary-hover font-medium inline-flex items-center gap-1"
      >
        {open ? <X size={14} /> : <Plus size={14} />}
        {open ? "Fermer" : "Synchroniser un nouveau board"}
      </button>

      {open && (
        <div className="mt-3 border border-border rounded-lg divide-y divide-border overflow-hidden">
          {isLoading && (
            <p className="px-3 py-3 text-sm text-muted-foreground">Chargement des boards Trello…</p>
          )}

          {!isLoading && unsyncedBoards.length === 0 && (
            <p className="px-3 py-3 text-sm text-muted-foreground">
              Tous vos boards Trello accessibles sont déjà synchronisés.
            </p>
          )}

          {unsyncedBoards.map((b) => (
            <div key={b.id} className="flex items-center justify-between px-3 py-2.5">
              <span className="text-sm text-foreground">{b.name}</span>
              <button
                type="button"
                onClick={() => handleSync(b)}
                disabled={syncingId === b.id}
                className="text-xs font-medium text-white bg-primary hover:bg-primary-hover rounded-md px-2.5 py-1.5 inline-flex items-center gap-1.5 disabled:opacity-50"
              >
                {syncingId === b.id ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                Synchroniser
              </button>
            </div>
          ))}
        </div>
      )}

      {error && <p className="text-xs text-destructive mt-2">{error}</p>}
    </div>
  );
}