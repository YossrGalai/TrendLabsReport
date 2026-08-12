import { useQuery, useMutation } from "@tanstack/react-query";
import {
  fetchBoards,
  fetchProjects,
  fetchSprintsInMonth,
  generateReport,
  fetchReportHistory,
  fetchTrelloBoards,
  syncBoard,
  fetchSyncStatus,
} from "../api/reports";

export const useBoards = () =>
  useQuery({ queryKey: ["boards"], queryFn: fetchBoards });

export const useProjects = (trelloBoardId) =>
  useQuery({
    queryKey: ["projects", trelloBoardId],
    queryFn: () => fetchProjects(trelloBoardId),
    enabled: !!trelloBoardId,
  });

// Sprints suggérés pour board+projet+mois — se recalcule à chaque changement de l'un de
// ces 4 paramètres (queryKey), enabled seulement quand les 4 sont renseignés.
export const useSprintsInMonth = (trelloBoardId, projectLabelId, month, year) =>
  useQuery({
    queryKey: ["sprintsInMonth", trelloBoardId, projectLabelId, month, year],
    queryFn: () => fetchSprintsInMonth(trelloBoardId, projectLabelId, month, year),
    enabled: !!trelloBoardId && projectLabelId != null,
  });

export const useGenerateReport = (trelloBoardId) =>
  useMutation({
    mutationFn: (payload) => generateReport(trelloBoardId, payload),
  });

export const useReportHistory = (trelloBoardId, projectLabelId) =>
  useQuery({
    queryKey: ["reportHistory", trelloBoardId, projectLabelId],
    queryFn: () => fetchReportHistory(trelloBoardId, projectLabelId),
    enabled: !!trelloBoardId,
  });

export const useSyncBoard = () =>
  useMutation({ mutationFn: (trelloBoardId) => syncBoard(trelloBoardId) });

export const useTrelloBoards = (enabled) =>
  useQuery({ queryKey: ["trelloBoards"], queryFn: fetchTrelloBoards, enabled });

export const useSyncStatus = (trelloBoardId) =>
  useQuery({
    queryKey: ["syncStatus", trelloBoardId],
    queryFn: () => fetchSyncStatus(trelloBoardId),
    enabled: !!trelloBoardId,
  });