import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2, Download, RefreshCw } from "lucide-react";
import { useBoards, useProjects, useSprintsInMonth, useGenerateReport, useSyncBoard } from "../../../hooks/useReports";
import { downloadReport } from "../../../api/reports";
import { SprintNumbersInput } from "./SprintNumbersInput";
import { SyncNewBoardPanel } from "./SyncNewBoardPanel";

const inputClass =
  "w-full rounded-lg border border-border bg-white px-3 py-2.5 text-foreground font-body text-sm focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary disabled:bg-muted disabled:text-muted-foreground transition-colors";
const labelClass = "block text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5";

export function ReportGeneratorForm() {
  const [trelloBoardId, setTrelloBoardId] = useState("");
  const [projectLabelId, setProjectLabelId] = useState(null);
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [year, setYear] = useState(new Date().getFullYear());
  const [sprintNumbers, setSprintNumbers] = useState([]);
  const [chefDeProjet, setChefDeProjet] = useState("");

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [extraBoardEnabled, setExtraBoardEnabled] = useState(false);
  const [extraBoardTrelloId, setExtraBoardTrelloId] = useState("");
  const [extraBoardProjectLabelId, setExtraBoardProjectLabelId] = useState("");
  const [extraProjectEnabled, setExtraProjectEnabled] = useState(false);
  const [extraProjectTrelloBoardId, setExtraProjectTrelloBoardId] = useState("");
  const [extraProjectLabelId, setExtraProjectLabelId] = useState("");

  const [status, setStatus] = useState("idle");
  const [reportRunId, setReportRunId] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);
  const [syncMsg, setSyncMsg] = useState(null);

  const queryClient = useQueryClient();
  const { data: boards, isLoading: loadingBoards } = useBoards();
  const { data: projects, isLoading: loadingProjects } = useProjects(trelloBoardId || null);
  const { data: extraBoardProjects } = useProjects(extraBoardEnabled ? extraBoardTrelloId || null : null);
  const { data: extraProjectBoardProjects } = useProjects(
    extraProjectEnabled ? extraProjectTrelloBoardId || trelloBoardId || null : null
  );
  const generateMutation = useGenerateReport(trelloBoardId);
  const syncMutation = useSyncBoard();
  const { data: suggestedSprints, isLoading: loadingSprints } = useSprintsInMonth(
    trelloBoardId || null, projectLabelId, month, year
  );

  // Pré-remplit dès que la suggestion arrive (ou change suite à un changement de board/
  // projet/mois/année) — l'admin garde la main pour ajouter/retirer via SprintNumbersInput,
  // exactement comme avant, seul le point de départ change. Si plus de 6 sprints sont
  // trouvés (max physique du template Gantt), on NE pré-sélectionne rien automatiquement —
  // choisir 6 sprints parmi N pour l'admin serait arbitraire ; il les ajoute lui-même via
  // les suggestions cliquables affichées dans ce cas (cf. JSX plus bas).
  useEffect(() => {
    if (suggestedSprints && suggestedSprints.length <= 6) {
      setSprintNumbers(suggestedSprints);
    }
  }, [suggestedSprints]);

  const selectedBoard = boards?.find((b) => b.trello_id === trelloBoardId);

  const isFormValid =
    trelloBoardId &&
    projectLabelId !== null &&
    sprintNumbers.length >= 1 &&
    sprintNumbers.length <= 6 &&
    (!extraProjectEnabled || extraProjectLabelId !== "");

  const handleSync = async () => {
    if (!trelloBoardId) return;
    setSyncMsg(null);
    try {
      await syncMutation.mutateAsync(trelloBoardId);
      await queryClient.invalidateQueries({ queryKey: ["boards"] });
      setSyncMsg({ type: "success", text: "Synchronisation réussie." });
    } catch (e) {
      setSyncMsg({
        type: "error",
        text: e instanceof Error ? e.message : "Échec de la synchronisation",
      });
    }
  };

  const handleGenerate = async () => {
    if (!isFormValid) return;
    setStatus("generating");
    setErrorMsg(null);
    setReportRunId(null);

    const payload = {
      project_label_id: projectLabelId,
      month,
      year,
      sprint_numbers: sprintNumbers,
      chef_de_projet: chefDeProjet || undefined,
      extra_boards:
        extraBoardEnabled && extraBoardTrelloId
          ? [{ trello_board_id: extraBoardTrelloId, project_label_id: extraBoardProjectLabelId === "" ? null : Number(extraBoardProjectLabelId) }]
          : [],
      extra_project:
        extraProjectEnabled && extraProjectLabelId !== ""
          ? { trello_board_id: extraProjectTrelloBoardId || null, project_label_id: Number(extraProjectLabelId) }
          : null,
    };

    try {
      const res = await generateMutation.mutateAsync(payload);
      if (res.success && res.report_run_id) {
        setReportRunId(res.report_run_id);
        setStatus("ready");
        queryClient.invalidateQueries({ queryKey: ["reportHistory", trelloBoardId, projectLabelId] });
      } else {
        setErrorMsg(res.error ?? "Erreur inconnue lors de la génération");
        setStatus("error");
      }
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Erreur réseau");
      setStatus("error");
    }
  };

  const handleDownload = async () => {
    if (!reportRunId) return;
    try {
      await downloadReport(reportRunId);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Échec du téléchargement");
      setStatus("error");
    }
  };

  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-1">TrendLabs</p>
      <h1 className="text-3xl font-display font-semibold text-foreground">Générer un rapport mensuel</h1>
      <p className="text-sm text-muted-foreground mt-1 mb-6">
        Sélectionnez un board synchronisé, un projet et une période pour produire le rapport Excel.
      </p>

      <div className="bg-card border border-border rounded-2xl shadow-[var(--shadow-card)] p-8 space-y-6">
        <div>
          <label className={labelClass}>Board Trello</label>
          <div className="flex gap-2">
            <select
              className={inputClass + " flex-1"}
              value={trelloBoardId}
              onChange={(e) => {
                setTrelloBoardId(e.target.value);
                setProjectLabelId(null);
                setSprintNumbers([]);
                setStatus("idle");
                setSyncMsg(null);
              }}
              disabled={loadingBoards}
            >
              <option value="">Sélectionner un board</option>
              {boards?.map((b) => (
                <option key={b.trello_id} value={b.trello_id}>{b.name}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleSync}
              disabled={!trelloBoardId || syncMutation.isPending}
              title="Synchroniser ce board avec Trello"
              className="shrink-0 p-2.5 rounded-lg border border-border text-muted-foreground hover:text-primary hover:border-primary transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <RefreshCw size={18} className={syncMutation.isPending ? "animate-spin" : ""} />
            </button>
          </div>
          <div className="flex items-center justify-between mt-1.5">
            {selectedBoard?.last_sync_at ? (
              <p className="text-xs text-muted-foreground font-mono">
                Dernière synchro : {new Date(selectedBoard.last_sync_at).toLocaleString("fr-FR")}
              </p>
            ) : selectedBoard ? (
              <p className="text-xs text-warning">Jamais synchronisé</p>
            ) : <span />}
            {syncMsg && (
              <p className={`text-xs ${syncMsg.type === "error" ? "text-destructive" : "text-success"}`}>
                {syncMsg.type === "error" ? `Erreur : ${syncMsg.text}` : syncMsg.text}
              </p>
            )}
          </div>
          <div className="mt-2">
            <SyncNewBoardPanel
              syncedBoards={boards}
              onSynced={(trelloId) => {
                setTrelloBoardId(trelloId);
                setProjectLabelId(null);
                setSprintNumbers([]);
                setStatus("idle");
              }}
            />
          </div>
        </div>

        <div>
          <label className={labelClass}>Projet</label>
          <select
            className={inputClass}
            value={projectLabelId ?? ""}
            onChange={(e) => { setProjectLabelId(e.target.value ? Number(e.target.value) : null); setSprintNumbers([]); }}
            disabled={!trelloBoardId || loadingProjects}
          >
            <option value="">Sélectionner un projet</option>
            {projects?.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelClass}>Mois</label>
            <select className={inputClass} value={month} onChange={(e) => setMonth(Number(e.target.value))}>
              {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => (
                <option key={m} value={m}>{new Date(2000, m - 1).toLocaleString("fr-FR", { month: "long" })}</option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelClass}>Année</label>
            <input type="number" className={inputClass + " font-mono"} value={year} onChange={(e) => setYear(Number(e.target.value))} min={2020} max={2100} />
          </div>
        </div>

        <div>
          <SprintNumbersInput value={sprintNumbers} onChange={setSprintNumbers} />
          {trelloBoardId && projectLabelId !== null && (
            loadingSprints ? (
              <p className="text-xs text-muted-foreground mt-1.5">Détection des sprints du mois…</p>
            ) : suggestedSprints && suggestedSprints.length > 6 ? (
              <div className="mt-2">
                <p className="text-xs text-warning mb-1.5">
                  {suggestedSprints.length} sprints trouvés pour ce mois — choisissez-en 6 maximum (limite du template) :
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {suggestedSprints.map((n) => {
                    const checked = sprintNumbers.includes(n);
                    const atLimit = sprintNumbers.length >= 6;
                    return (
                      <button
                        key={n}
                        type="button"
                        disabled={!checked && atLimit}
                        onClick={() =>
                          setSprintNumbers((prev) =>
                            checked ? prev.filter((s) => s !== n) : [...prev, n].sort((a, b) => a - b)
                          )
                        }
                        className={
                          "rounded-full font-mono text-xs px-2.5 py-1 transition-colors " +
                          (checked
                            ? "bg-foreground text-background"
                            : "bg-muted text-muted-foreground hover:bg-muted/70 disabled:opacity-40 disabled:cursor-not-allowed")
                        }
                      >
                        Sprint {n}
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : suggestedSprints && suggestedSprints.length === 0 ? (
              <p className="text-xs text-muted-foreground mt-1.5">Aucun sprint trouvé pour ce mois — ajoutez-les manuellement.</p>
            ) : null
          )}
        </div>

        <div>
          <label className={labelClass}>Chef de projet <span className="normal-case font-normal text-muted-foreground/70">(optionnel)</span></label>
          <input type="text" className={inputClass} value={chefDeProjet} onChange={(e) => setChefDeProjet(e.target.value)} />
        </div>

        <div className="border-t border-border pt-5">
          <button type="button" onClick={() => setShowAdvanced(!showAdvanced)} className="flex items-center gap-1.5 text-sm font-medium text-foreground hover:text-primary transition-colors">
            {showAdvanced ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            Options avancées
          </button>

          {showAdvanced && (
            <div className="mt-4 space-y-5 pl-1">
              <div>
                <label className="flex items-center gap-2 text-sm font-medium text-foreground mb-2">
                  <input type="checkbox" checked={extraBoardEnabled} onChange={(e) => setExtraBoardEnabled(e.target.checked)} className="accent-primary" />
                  Ajouter un board supplémentaire
                </label>
                {extraBoardEnabled && (
                  <div className="space-y-2 pl-6">
                    <select className={inputClass} value={extraBoardTrelloId} onChange={(e) => setExtraBoardTrelloId(e.target.value)}>
                      <option value="">Sélectionner un board</option>
                      {boards?.filter((b) => b.trello_id !== trelloBoardId).map((b) => (
                        <option key={b.trello_id} value={b.trello_id}>{b.name}</option>
                      ))}
                    </select>
                    <select className={inputClass} value={extraBoardProjectLabelId} onChange={(e) => setExtraBoardProjectLabelId(e.target.value)} disabled={!extraBoardTrelloId}>
                      <option value="">Projet auto (résolu par nom)</option>
                      {extraBoardProjects?.map((p) => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>

              <div>
                <label className="flex items-center gap-2 text-sm font-medium text-foreground mb-2">
                  <input type="checkbox" checked={extraProjectEnabled} onChange={(e) => setExtraProjectEnabled(e.target.checked)} className="accent-primary" />
                  Ajouter un projet supplémentaire (module à part)
                </label>
                {extraProjectEnabled && (
                  <div className="space-y-2 pl-6">
                    <select className={inputClass} value={extraProjectTrelloBoardId} onChange={(e) => setExtraProjectTrelloBoardId(e.target.value)}>
                      <option value="">Board principal {trelloBoardId ? "(actuel)" : ""}</option>
                      {boards?.map((b) => (
                        <option key={b.trello_id} value={b.trello_id}>{b.name}</option>
                      ))}
                    </select>
                    <select className={inputClass} value={extraProjectLabelId} onChange={(e) => setExtraProjectLabelId(e.target.value)}>
                      <option value="">Sélectionner un projet (obligatoire)</option>
                      {extraProjectBoardProjects?.map((p) => (
                        <option key={p.id} value={p.id}>{p.name}</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3 pt-1">
          <button
            type="button"
            onClick={handleGenerate}
            disabled={!isFormValid || status === "generating"}
            className="bg-primary hover:bg-primary-hover text-primary-foreground font-medium text-sm px-5 py-2.5 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center gap-2"
          >
            {status === "generating" && <Loader2 size={16} className="animate-spin" />}
            Préparer le fichier
          </button>

          {status === "ready" && (
            <button type="button" onClick={handleDownload} className="bg-success hover:opacity-90 text-white font-medium text-sm px-5 py-2.5 rounded-lg transition-colors inline-flex items-center gap-2">
              <Download size={16} />
              Télécharger le rapport
            </button>
          )}
        </div>

        {status === "error" && errorMsg && (
          <p className="text-destructive text-sm border border-destructive/20 bg-destructive/5 rounded-lg px-3 py-2">{errorMsg}</p>
        )}
      </div>
    </>
  );
}