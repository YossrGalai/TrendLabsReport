import { useQuery, useMutation } from "@tanstack/react-query";
import {
  fetchAnnualSprints,
  generateAnnualReport,
  fetchAnnualReportStatus,
} from "../api/annualReports";

export const useAnnualSprints = (dateStart, dateEnd, boardIds) =>
  useQuery({
    queryKey: ["annualSprints", dateStart, dateEnd, boardIds],
    queryFn: () => fetchAnnualSprints(dateStart, dateEnd, boardIds),
    enabled: !!dateStart && !!dateEnd,
  });

export const useGenerateAnnualReport = () =>
  useMutation({ mutationFn: (payload) => generateAnnualReport(payload) });

// Poll le statut tant que le rapport n'est pas terminé — la génération annuelle tourne en
// tâche de fond côté backend (annual_report_endpoints.py), contrairement au mensuel qui
// répond directement "prêt". S'arrête de lui-même dès que status devient "done" ou "error".
// NOTE : signature écrite pour @tanstack/react-query v5 (refetchInterval reçoit l'objet
// `query`). Si le projet est encore en v4, remplacer par
// `refetchInterval: (data) => (data?.status === "done" || data?.status === "error") ? false : 2000`
export const useAnnualReportStatus = (reportRunId) =>
  useQuery({
    queryKey: ["annualReportStatus", reportRunId],
    queryFn: () => fetchAnnualReportStatus(reportRunId),
    enabled: !!reportRunId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "done" || status === "error" ? false : 2000;
    },
  });