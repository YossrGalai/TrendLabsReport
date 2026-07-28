import { Tags, Timer, GitPullRequest, FileSearch, UserX, CheckCircle2, RotateCcw, Clock } from "lucide-react";

const rules = [
  {
    icon: FileSearch,
    title: "Résolution des projets",
    description:
      "Les projets valides sont déterminés par les labels posés sur la carte nommée « Description », dans la liste « Product Backlog » du board. Relu à chaque synchronisation : si ces labels changent sur Trello, la liste des projets suit automatiquement.",
  },
  {
    icon: Timer,
    title: "Début d'un ticket",
    description:
      "Premier passage en liste « En cours » (stage IN_PROGRESS). Un simple aller-retour via Stand By/Rétrospective sans jamais atteindre un stage « terminé » repousse le début au dernier retour en cours — le vrai travail n'a repris qu'à ce moment-là.",
  },
  {
    icon: GitPullRequest,
    title: "Fin d'un ticket — la PR est prioritaire",
    description:
      "Une carte est considérée terminée dès qu'un commentaire « PR #.. created by .. » est détecté, à SA date — peu importe si le déplacement Trello vers une liste terminée arrive bien plus tard. Sans PR, c'est le dernier stage « terminé » atteint qui fait foi.",
  },
  {
    icon: UserX,
    title: "Exclusion du Product Owner",
    description:
      "Les actions faites par le PO (liste d'IDs Trello dédiée) ne comptent jamais pour calculer le début/la fin d'un ticket — seules les actions des propriétaires actuels de la carte sont prises en compte. Un déplacement fait par le PO n'avance donc pas la date de fin.",
  },
  {
    icon: CheckCircle2,
    title: "Avancement à 100 %",
    description:
      "Uniquement si la carte est dans le stage « En prod » (IN_PROD). Attention : un stage nommé « Terminé & Validé (préprod) » ne compte volontairement PAS comme terminé, malgré son nom.",
  },
  {
    icon: RotateCcw,
    title: "Régressions exclues du rapport",
    description:
      "Une carte qui repart en Product/Sprint Backlog après avoir démarré est réinitialisée (retirée du rapport, comme si elle n'avait jamais commencé). Une carte qui passe un jour de « En cours » vers Stand By/Rétrospective est exclue définitivement, même si elle est refermée plus tard.",
  },
  {
    icon: Tags,
    title: "Labels ignorés dans le classement par module",
    description:
      "Les labels Priorité, OK, À clôturer, Bug, Retour, Test, Urgent, Demande client et Sentry sont ignorés lors du regroupement par module — ce sont des labels de statut, pas de vrais modules. Une carte qui n'a plus que ces labels retombe en « Non classé ».",
  },
  {
    icon: Clock,
    title: "Déplacement avant 9h = veille",
    description:
      "Un passage vers un stage « terminé » avant 9h (heure de Tunis) est attribué à la veille : un dev qui oublie de déplacer sa carte le soir et le fait le lendemain matin en arrivant ne doit pas gagner une journée de travail fictive. Cette règle ne s'applique pas au commentaire PR, déjà horodaté au moment réel du travail.",
  },
];

export function SettingsPage() {
  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-1">TrendLabs</p>
      <h1 className="text-3xl font-display font-semibold text-foreground">Règles de génération du rapport</h1>
      <p className="text-sm text-muted-foreground mt-1 mb-6"></p>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {rules.map(({ icon: Icon, title, description }) => (
          <div
            key={title}
            className="rounded-2xl border border-border bg-card p-6 shadow-[var(--shadow-card)]"
          >
            <div className="mb-2 flex items-center gap-2 text-primary">
              <Icon size={18} />
              <h2 className="text-sm font-semibold text-foreground">{title}</h2>
            </div>
            <p className="text-sm text-muted-foreground">{description}</p>
          </div>
        ))}
      </div>
    </>
  );
}