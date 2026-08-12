import { useState } from "react";
import { useBoards, useProjects } from "../../../hooks/useReports";
import { ReportHistory } from "./ReportHistory";

const inputClass =
  "w-full rounded-lg border border-border bg-white px-3 py-2.5 text-foreground font-body text-sm focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary disabled:bg-muted disabled:text-muted-foreground transition-colors";
const labelClass = "block text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5";

export function HistoryPage() {
  const [trelloBoardId, setTrelloBoardId] = useState("");
  const [projectLabelId, setProjectLabelId] = useState(null);

  const { data: boards, isLoading: loadingBoards } = useBoards();
  const { data: projects, isLoading: loadingProjects } = useProjects(trelloBoardId || null);

  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-1">TrendLabs</p>
      <h1 className="text-3xl font-display font-semibold text-foreground">Historique des rapports</h1>
      <p className="text-sm text-muted-foreground mt-1 mb-6">
        Sélectionnez un board (et éventuellement un projet) pour consulter les rapports déjà générés.
      </p>

      <div className="bg-card border border-border rounded-2xl shadow-[var(--shadow-card)] p-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className={labelClass}>Board Trello</label>
          <select
            className={inputClass}
            value={trelloBoardId}
            onChange={(e) => {
              setTrelloBoardId(e.target.value);
              setProjectLabelId(null);
            }}
            disabled={loadingBoards}
          >
            <option value="">Sélectionner un board</option>
            {boards?.map((b) => (
              <option key={b.trello_id} value={b.trello_id}>{b.name}</option>
            ))}
          </select>
        </div>

        <div>
          <label className={labelClass}>
            Projet <span className="normal-case font-normal text-muted-foreground/70">(optionnel)</span>
          </label>
          <select
            className={inputClass}
            value={projectLabelId ?? ""}
            onChange={(e) => setProjectLabelId(e.target.value ? Number(e.target.value) : null)}
            disabled={!trelloBoardId || loadingProjects}
          >
            <option value="">Tous les projets</option>
            {projects?.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
      </div>

      {!trelloBoardId && (
        <p className="mt-6 text-sm text-muted-foreground">
          Choisissez un board ci-dessus pour afficher son historique de rapports.
        </p>
      )}

      <ReportHistory trelloBoardId={trelloBoardId || null} projectLabelId={projectLabelId} projects={projects} />
    </>
  );
}