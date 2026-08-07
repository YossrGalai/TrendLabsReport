import { NavLink, useLocation } from "react-router-dom";
import { FileSpreadsheet, CalendarRange, LayoutGrid, History, Settings, Layers, HelpCircle } from "lucide-react";

const nav = [
  { label: "Rapport mensuel", icon: FileSpreadsheet, to: "/" },
  { label: "Rapport annuel", icon: CalendarRange, to: "/rapport-annuel" },
  { label: "Boards synchronisés", icon: Layers, to: "/boards" },
  { label: "Historique", icon: History, to: "/historique" },
  { label: "Tableau de bord", icon: LayoutGrid, to: "/dashboard" },
  { label: "Paramètres", icon: Settings, to: "/parametres" },
];

const breadcrumbByPath = {
  "/": ["Rapports", "Rapport mensuel"],
  "/rapport-annuel": ["Rapports", "Rapport annuel"],
  "/boards": ["Rapports", "Boards synchronisés"],
  "/historique": ["Rapports", "Historique"],
  "/dashboard": ["Rapports", "Tableau de bord"],
  "/parametres": ["Rapports", "Règles de génération"],
};

// Les pages "formulaire" (mensuel ET annuel) sont un peu plus larges que les autres pages,
// qui restent sur max-w-5xl pour rester lisibles (tableaux, listes...).
const WIDE_ROUTES = new Set(["/", "/rapport-annuel"]);

export function AppShell({ children }) {
  const { pathname } = useLocation();
  const [section, page] = breadcrumbByPath[pathname] ?? ["Rapports", null];
  const maxWidthClass = WIDE_ROUTES.has(pathname) ? "max-w-6xl" : "max-w-5xl";

  return (
    <div className="flex min-h-screen bg-background">
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col bg-sidebar px-4 py-6 text-sidebar-foreground lg:flex">
        <div className="flex items-center gap-3 px-2">
          <div className="flex size-9 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
            <FileSpreadsheet className="size-4.5" />
          </div>
          <div className="leading-tight">
            <p className="text-sm font-bold tracking-tight">TrendLabs</p>
            <p className="text-xs text-sidebar-foreground/60">Reporting Suite</p>
          </div>
        </div>
        <nav className="mt-8 flex flex-col gap-1">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors " +
                (isActive
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground")
              }
            >
              <item.icon className="size-4" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* Pointe vers la page Paramètres, qui documente les règles de génération du rapport */}
        <NavLink
          to="/parametres"
          className="mt-auto block rounded-xl bg-sidebar-accent/60 p-4 transition-colors hover:bg-sidebar-accent"
        >
          <HelpCircle className="size-4 text-sidebar-foreground/70" />
          <p className="mt-2 text-sm font-semibold">Besoin d'aide ?</p>
          <p className="mt-1 text-xs text-sidebar-foreground/60">
            Consultez les règles de génération des rapports.
          </p>
        </NavLink>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-16 items-center border-b border-border bg-card/85 px-6 backdrop-blur">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="font-semibold text-foreground lg:hidden">TrendLabs</span>
            <span className="hidden lg:inline">{section}</span>
            {page && (
              <>
                <span className="hidden lg:inline text-border">/</span>
                <span className="hidden font-medium text-foreground lg:inline">{page}</span>
              </>
            )}
          </div>
        </header>
        <main className="flex-1 px-6 py-10">
          <div className={`mx-auto w-full ${maxWidthClass}`}>{children}</div>
        </main>
      </div>
    </div>
  );
}