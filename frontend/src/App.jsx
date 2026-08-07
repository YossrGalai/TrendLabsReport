import { Routes, Route } from "react-router-dom";
import { AppShell } from "./features/reports/components/AppShell";
import { ReportGeneratorForm } from "./features/reports/components/ReportGeneratorForm";
import { AnnualReportGeneratorForm } from "./features/reports/components/AnnualReportGeneratorForm";
import { SyncedBoardsPage } from "./features/boards/components/SyncedBoardsPage";
import { HistoryPage } from "./features/reports/components/HistoryPage";
import { DashboardPage } from "./features/dashboards/components/DashboardPage";
import { SettingsPage } from "./features/settings/components/SettingsPage";

function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<ReportGeneratorForm />} />
        <Route path="/rapport-annuel" element={<AnnualReportGeneratorForm />} />
        <Route path="/boards" element={<SyncedBoardsPage />} />
        <Route path="/historique" element={<HistoryPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/parametres" element={<SettingsPage />} />
      </Routes>
    </AppShell>
  );
}

export default App;