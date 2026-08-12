import { Routes, Route } from "react-router-dom";
import { AppShell } from "./features/reports/components/AppShell";
import { ReportGeneratorForm } from "./features/reports/components/ReportGeneratorForm";
import { AnnualReportGeneratorForm } from "./features/reports/components/AnnualReportGeneratorForm";
import { SyncedBoardsPage } from "./features/boards/components/SyncedBoardsPage";
import { HistoryPage } from "./features/reports/components/HistoryPage";
import { DashboardPage } from "./features/dashboards/components/DashboardPage";
import { SettingsPage } from "./features/settings/components/SettingsPage";
import { LoginPage } from "./features/auth/components/LoginPage";
import { RequireAuth } from "./features/auth/context/AuthContext";

function App() {
  return (
    <Routes>
      {/* Route publique — PAS enveloppée dans AppShell (pas de sidebar sur l'écran de
          connexion) ni dans RequireAuth (sinon boucle infinie de redirection). */}
      <Route path="/login" element={<LoginPage />} />

      {/* Tout le reste passe par RequireAuth : redirige vers /login si pas connecté.
          path="/*" + <Routes> imbriquées = pattern standard react-router v6 pour protéger
          un groupe entier de routes existantes sans les modifier une par une. */}
      <Route
        path="/*"
        element={
          <RequireAuth>
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
          </RequireAuth>
        }
      />
    </Routes>
  );
}

export default App;