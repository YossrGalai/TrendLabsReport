export interface Board {
  id: number;
  name: string;
  trello_id: string;
  last_sync_at: string | null;
}

export interface Project {
  id: number;
  name: string;
  color: string | null;
}

export interface ExtraBoardInput {
  trello_board_id: string;
  project_label_id?: number | null;
}

export interface ExtraProjectInput {
  trello_board_id?: string | null;
  project_label_id: number;
}

export interface ReportGenerateRequest {
  project_label_id: number;
  month: number;
  year: number;
  sprint_numbers: number[];
  chef_de_projet?: string;
  extra_boards?: ExtraBoardInput[];
  extra_project?: ExtraProjectInput | null;
}

export interface ReportGenerateResponse {
  success: boolean;
  report_run_id: number | null;
  file_path: string | null;
  error: string | null;
  timestamp: string;
}

export type ReportStatus = "RUNNING" | "DONE" | "ERROR";

export interface ReportRun {
  id: number;
  board_id: number;
  project_label_id: number;
  report_month: number;
  report_year: number;
  sprint_numbers: number[];
  status: ReportStatus;
  file_path: string | null;
  generated_at: string | null;
  error_message: string | null;
}