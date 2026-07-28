import { useQuery, useMutation } from "@tanstack/react-query";
import {
  fetchBoards,
  fetchProjects,
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