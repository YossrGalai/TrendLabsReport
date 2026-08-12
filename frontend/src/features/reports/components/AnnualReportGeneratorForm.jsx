import { useEffect, useState } from "react";
import { Loader2, Download, X } from "lucide-react";
import { useBoards } from "../../../hooks/useReports";
import {
  useAnnualSprints,
  useGenerateAnnualReport,
  useAnnualReportStatus,
} from "../../../hooks/useAnnualReports";
import { downloadAnnualReport } from "../../../api/annualReports";

// Mêmes classes que ReportGeneratorForm.jsx, dupliquées ici plutôt qu'extraites en composant
// partagé — 2 constantes, pas assez pour justifier un fichier commun pour l'instant.
const inputClass =
  "w-full rounded-lg border border-border bg-white px-3 py-2.5 text-foreground font-body text-sm focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary disabled:bg-muted disabled:text-muted-foreground transition-colors";
const labelClass = "block text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1.5";

export function AnnualReportGeneratorForm() {
  // Chrome déclenche onChange à CHAQUE chiffre tapé dans un <input type="date"> (année en
  // cours de frappe : "0", "00", "002"...), pas seulement une fois la date complète. Sans
  // filtre, ces valeurs intermédiaires partent telles quelles vers le backend (d'où les
  // date_end="0002-07-31" observés). On n'accepte que "" ou une date à 4 chiffres d'année
  // plausible.
  const isPlausibleDate = (v) => v === "" || (/^\d{4}-\d{2}-\d{2}$/.test(v) && Number(v.slice(0, 4)) >= 2000);

  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");

  // [] = "tous les boards" (board_ids non envoyé au backend). L'admin peut décocher "Tous
  // les boards" pour faire apparaître la liste et en sélectionner un sous-ensemble.
  const [allBoards, setAllBoards] = useState(true);
  const [selectedBoardIds, setSelectedBoardIds] = useState([]);

  const [selectedSprints, setSelectedSprints] = useState(null); // null = pas encore initialisé depuis le fetch
  const [reportRunId, setReportRunId] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);

  const { data: boards, isLoading: loadingBoards } = useBoards();
  const boardIdsForQuery = allBoards ? [] : selectedBoardIds;
  const { data: sprintsInPeriod, isLoading: loadingSprints } = useAnnualSprints(
    dateStart || null, dateEnd || null, boardIdsForQuery
  );
  const generateMutation = useGenerateAnnualReport();
  const { data: runStatus } = useAnnualReportStatus(reportRunId);

  // Dès que la liste des sprints de la période arrive (ou change suite à un changement de
  // dates/boards), on ré-initialise la sélection à "tout coché" — l'admin part d'une base
  // complète et décoche ce qu'il veut exclure, plutôt que de partir de rien.
  useEffect(() => {
    if (sprintsInPeriod) setSelectedSprints(sprintsInPeriod);
  }, [sprintsInPeriod]);

  const toggleBoard = (id) => {
    setSelectedBoardIds((prev) =>
      prev.includes(id) ? prev.filter((b) => b !== id) : [...prev, id]
    );
  };

  const toggleSprint = (n) => {
    setSelectedSprints((prev) => (prev ?? []).filter((s) => s !== n).length === (prev ?? []).length
      ? [...(prev ?? []), n].sort((a, b) => a - b)
      : (prev ?? []).filter((s) => s !== n)
    );
  };

  const status = generateMutation.isPending
    ? "generating"
    : !reportRunId
    ? (generateMutation.isError ? "error" : "idle")
    : runStatus?.status === "done"
    ? "ready"
    : runStatus?.status === "error"
    ? "error"
    : "generating";

  const isFormValid =
    !!dateStart &&
    !!dateEnd &&
    dateEnd >= dateStart &&
    (allBoards || selectedBoardIds.length > 0) &&
    (selectedSprints?.length ?? 0) >= 1;

  const handleGenerate = async () => {
    if (!isFormValid) return;
    setErrorMsg(null);
    setReportRunId(null);

    const payload = {
      date_start: dateStart,
      date_end: dateEnd,
      board_ids: allBoards ? undefined : selectedBoardIds,
      sprint_numbers: selectedSprints,
    };

    try {
      const run = await generateMutation.mutateAsync(payload);
      setReportRunId(run.id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Erreur réseau");
    }
  };

  const handleDownload = async () => {
    if (!reportRunId) return;
    try {
      await downloadAnnualReport(reportRunId);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : "Échec du téléchargement");
    }
  };

  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-1">TrendLabs</p>
      <h1 className="text-3xl font-display font-semibold text-foreground">Générer un rapport annuel</h1>
      <p className="text-sm text-muted-foreground mt-1 mb-6">
        Choisissez une période : tous les boards et tous les projets synchronisés y sont inclus.
      </p>

      <div className="bg-card border border-border rounded-2xl shadow-[var(--shadow-card)] p-8 space-y-6">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelClass}>Date de début</label>
            <input
              type="date"
              className={inputClass}
              value={dateStart}
              max={dateEnd || undefined}
              onChange={(e) => { if (isPlausibleDate(e.target.value)) { setDateStart(e.target.value); setReportRunId(null); } }}
            />
          </div>
          <div>
            <label className={labelClass}>Date de fin</label>
            <input
              type="date"
              className={inputClass}
              value={dateEnd}
              min={dateStart || undefined}
              onChange={(e) => { if (isPlausibleDate(e.target.value)) { setDateEnd(e.target.value); setReportRunId(null); } }}
            />
          </div>
        </div>

        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-foreground mb-2">
            <input
              type="checkbox"
              checked={allBoards}
              onChange={(e) => { setAllBoards(e.target.checked); setReportRunId(null); }}
              className="accent-primary"
            />
            Tous les boards synchronisés
          </label>
          {!allBoards && (
            <div className="flex flex-wrap gap-2 pl-6">
              {loadingBoards && <span className="text-xs text-muted-foreground">Chargement…</span>}
              {boards?.map((b) => (
                <label
                  key={b.id}
                  className={
                    "flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs cursor-pointer transition-colors " +
                    (selectedBoardIds.includes(b.id)
                      ? "bg-foreground text-background border-foreground"
                      : "border-border text-muted-foreground hover:border-primary")
                  }
                >
                  <input
                    type="checkbox"
                    checked={selectedBoardIds.includes(b.id)}
                    onChange={() => { toggleBoard(b.id); setReportRunId(null); }}
                    className="hidden"
                  />
                  {b.name}
                </label>
              ))}
            </div>
          )}
        </div>

        <div>
          <label className={labelClass}>
            Sprints couverts par la période <span className="normal-case font-normal text-muted-foreground/70">(décochez ceux à exclure)</span>
          </label>
          {!dateStart || !dateEnd ? (
            <p className="text-xs text-muted-foreground">Choisissez d'abord une période.</p>
          ) : loadingSprints ? (
            <p className="text-xs text-muted-foreground">Chargement des sprints…</p>
          ) : !sprintsInPeriod?.length ? (
            <p className="text-xs text-warning">Aucun sprint trouvé sur cette période.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {sprintsInPeriod.map((n) => {
                const checked = selectedSprints?.includes(n) ?? false;
                return (
                  <button
                    key={n}
                    type="button"
                    onClick={() => { toggleSprint(n); setReportRunId(null); }}
                    className={
                      "flex items-center gap-1.5 rounded-full font-mono text-xs px-3 py-1 transition-colors " +
                      (checked
                        ? "bg-foreground text-background"
                        : "bg-muted text-muted-foreground line-through decoration-2")
                    }
                  >
                    Sprint {n}
                    {checked && <X size={14} />}
                  </button>
                );
              })}
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
            {status === "generating" ? "Génération en cours…" : "Préparer le fichier"}
          </button>

          {status === "ready" && (
            <button
              type="button"
              onClick={handleDownload}
              className="bg-success hover:opacity-90 text-white font-medium text-sm px-5 py-2.5 rounded-lg transition-colors inline-flex items-center gap-2"
            >
              <Download size={16} />
              Télécharger le rapport
            </button>
          )}
        </div>

        {status === "error" && (
          <p className="text-destructive text-sm border border-destructive/20 bg-destructive/5 rounded-lg px-3 py-2">
            {errorMsg || runStatus?.error_message || "Erreur inconnue lors de la génération"}
          </p>
        )}
      </div>
    </>
  );
}