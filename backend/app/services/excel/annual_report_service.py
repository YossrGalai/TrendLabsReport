"""
Service de génération du RAPPORT ANNUEL — multi-board, multi-projet, sur une plage de
dates libre (contrairement au mensuel, qui est verrouillé sur 1 board + 1 projet + des
sprint_numbers).

Conçu en COMPOSITION avec ExcelReportService (pas d'héritage, pas de modification de
excel_service.py) : on réutilise ses méthodes pures de calcul (dédup des jours travaillés,
regroupement par label, filtrage de période) via une instance interne `self._excel`.
Voir la docstring de generate_annual_report() pour le détail de ce qui est réutilisé tel
quel vs. réécrit spécifiquement pour l'annuel.

ADAPTER le chemin d'import ci-dessous (`app.services.excel.excel_service`) si le module
excel_service.py vit ailleurs dans votre arborescence — c'est une supposition basée sur le
commentaire trouvé en tête de ce fichier ("/app/app/services/excel/reports").
"""

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List as TypingList, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule
from sqlalchemy.orm import Session, joinedload

from app.models.models import Board, Card, LabelTypeEnum
from app.services.excel.excel_service import (
    BACKLOG_STAGES,
    DEFAULT_OUTPUT_DIR,
    EXCLUDED_STAGES,
    ExcelReportService,
)

logger = logging.getLogger(__name__)

# ============================================================================
# STYLES — délibérément indépendants de ceux du template mensuel (excel_service.py) :
# le rapport annuel n'utilise PAS le template Gantt (mise en page différente, cf.
# _build_workbook), donc pas de raison de dépendre de ses constantes visuelles internes.
# ============================================================================

HEADER_FONT = Font(name="Roboto", size=11, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
TOTAL_ROW_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
TOTAL_ROW_FONT = Font(name="Roboto", size=10, bold=True)
CELL_FONT = Font(name="Roboto", size=10)
ZEBRA_FILL = PatternFill(start_color="F3F6FB", end_color="F3F6FB", fill_type="solid")
TITLE_FONT = Font(name="Roboto", size=14, bold=True, color="1F4E78")
SUBTITLE_FONT = Font(name="Roboto", size=10, italic=True, color="6B7280")
_THIN = Side(style="thin", color="B7B7B7")
CELL_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")

# Échelle rouge (sous-occupé) -> jaune -> vert (occupation ~pleine) pour le taux
# d'occupation et le total annuel — lecture immédiate sans avoir à lire les chiffres.
OCCUPANCY_COLOR_SCALE = ColorScaleRule(
    start_type="min", start_color="F8696B",
    mid_type="percentile", mid_value=50, mid_color="FFEB84",
    end_type="max", end_color="63BE7B",
)


class AnnualReportService:
    """Génère le rapport Excel annuel (tous boards / tous projets / plage de dates libre)."""

    def __init__(self, output_dir: Optional[Path] = None):
        self._excel = ExcelReportService()  # composition : accès à ses méthodes pures
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # POINT D'ENTRÉE
    # ========================================================================

    def generate_annual_report(
        self,
        db: Session,
        date_start: date,
        date_end: date,
        board_ids: Optional[TypingList[int]] = None,
        sprint_numbers: Optional[TypingList[int]] = None,
    ) -> str:
        """Génère le rapport pour TOUS les boards (ou ceux listés dans board_ids), TOUS
        les projets confondus, sur [date_start, date_end].

        sprint_numbers : filtre optionnel supplémentaire — si fourni, ne garde que les
        cartes portant un label SPRINT dont le sprint_number est dans cette liste (même
        logique de filtre que le mensuel, cf. _fetch_cards_for_sprints, mais appliquée ICI
        en post-filtre Python sur card.labels déjà chargé, plutôt qu'en clause SQL, car les
        cartes de plusieurs boards sont déjà fusionnées à ce stade). Pensé pour le cas
        d'usage front : la période choisie propose par défaut TOUS les sprints qui s'y
        chevauchent (cf. list_sprints_in_period), et l'admin peut en décocher avant de
        lancer la génération — si sprint_numbers est None, aucun filtre sprint n'est
        appliqué (comportement par défaut, uniquement basé sur les dates).

        Réutilise tel quel (composition, self._excel.<methode>) :
        - _filter_cards_active_in_period : chevauchement carte/période
        - _exclude_cards_regressed_to_retrospective, _exclude_cards_in_excluded_lists,
          _exclude_regressed_cards : même pipeline d'exclusion que le mensuel
        - _group_by_label_type(cards, LabelTypeEnum.PROJECT) : regroupement par projet
          (au lieu de MODULE côté mensuel — la fonction est générique sur label_type)
        - _owner_list, _card_weekend_start_day : lecture des devs / jours week-end travaillés
        - _working_days_of_month(date_start, date_end, max_days=100_000) : liste des jours
          ouvrés de la période, EN DÉSACTIVANT le plafond GANTT_MAX_COLS (~23 jours) qui
          n'a de sens que pour la largeur physique de la grille Gantt du template mensuel —
          sur un an (~260 jours ouvrés), l'appeler avec le max_days par défaut tronquerait
          silencieusement les totaux au premier mois. Voir _card_active_days_in_annual
          ci-dessous pour le SEUL autre point d'adaptation nécessaire.

        PAS réutilisé (logique spécifique à l'annuel, écrite ci-dessous) :
        - fetch des cartes : _fetch_cards_for_sprints filtre par sprint_numbers ET par UN
          SEUL board ; ici on fetch par date sur PLUSIEURS boards, le filtre sprint (s'il est
          demandé) étant appliqué à part → _fetch_cards_for_board_in_period
        - _card_active_days_in : retourne un set() si completed_at est None (carte encore
          ouverte) — correct pour le mensuel (une carte ouverte "vit" jusqu'à la fin du mois
          par construction du Gantt), mais insuffisant ici car aucune fenêtre d'affichage
          équivalente n'existe → _card_active_days_in_annual, qui calcule une date de fin
          effective (completed_at, ou la borne de fin demandée / aujourd'hui si plus tôt)
          avant de réappliquer exactement la même boucle de dédup.
        - mise en page du classeur (_build_workbook) : le template Gantt mensuel n'a pas de
          sens pour une vue annuelle multi-projet ; nouvelle mise en page dédiée (vue
          d'ensemble + un onglet par projet).

        Retourne le chemin du fichier généré."""
        boards = self._fetch_boards(db, board_ids)
        if not boards:
            raise ValueError("Aucun board trouvé pour les board_ids demandés")

        working_days = set(self._excel._working_days_of_month(date_start, date_end, max_days=100_000))
        weekend_days = set(self._excel._weekend_days_of_period(date_start, date_end))

        # project_dev_days : { nom_projet: { nom_dev: set(jours calendaires uniques) } }
        # project_boards   : { nom_projet: {noms de boards ayant contribué à ce projet} }
        # On boucle PAR BOARD (plutôt que de fusionner toutes les cartes d'abord) pour
        # pouvoir tracer quel(s) board(s) alimentent chaque projet — perdu si on fusionne
        # avant de grouper par projet.
        project_dev_days: Dict[str, Dict[str, set]] = {}
        project_boards: Dict[str, set] = {}
        by_board = self._fetch_and_clean_cards_by_board(db, boards, date_start, date_end)
        for board, cards in by_board.items():
            if sprint_numbers:
                cards = self._filter_cards_by_sprints(cards, sprint_numbers)
            by_project = self._excel._group_by_label_type(cards, LabelTypeEnum.PROJECT)
            for project_name, project_cards in by_project.items():
                project_boards.setdefault(project_name, set()).add(board.name)
                dev_days = project_dev_days.setdefault(project_name, {})
                for card in project_cards:
                    owners = self._excel._owner_list(card) or ["(non assigné)"]
                    active = self._card_active_days_in_annual(card, working_days, date_end)
                    weekend = self._excel._card_weekend_start_day(card, weekend_days)
                    for owner in owners:
                        dev_days.setdefault(owner, set()).update(active)
                        if weekend:
                            dev_days[owner].update(weekend)

        wb = self._build_workbook(
            project_dev_days, project_boards, date_start, date_end,
            boards=boards, sprint_numbers=sprint_numbers, working_days_count=len(working_days),
        )

        output_path = (
            self.output_dir
            / f"rapport_annuel_{date_start:%Y-%m-%d}_{date_end:%Y-%m-%d}.xlsx"
        )
        wb.save(output_path)
        logger.info(f"✅ Rapport annuel généré : {output_path}")
        return str(output_path)

    # ========================================================================
    # REQUÊTE DES DONNÉES — spécifique à l'annuel
    # ========================================================================

    def _fetch_boards(self, db: Session, board_ids: Optional[TypingList[int]]) -> TypingList[Board]:
        query = db.query(Board)
        if board_ids:
            query = query.filter(Board.id.in_(board_ids))
        return query.all()

    def _fetch_cards_for_board_in_period(
        self, db: Session, board_id: int, date_start: date, date_end: date,
    ) -> TypingList[Card]:
        """Cartes du board en chevauchement avec [date_start, date_end], SANS filtre
        sprint (contrairement à _fetch_cards_for_sprints) ni tolérance de +-quelques jours
        (CARD_MONTH_TOLERANCE_DAYS n'a de sens que pour rattacher une carte à cheval à
        LEQUEL de deux mois voisins elle appartient — sur un an, ce cas ne se pose pas :
        une carte chevauchant la borne de la période est simplement incluse, comme le fait
        déjà _filter_cards_active_in_period appliqué juste après par l'appelant).
        Le tri par started_at et le chargement joinedload(list/labels/members) suivent le
        même pattern que _fetch_cards_for_sprints, pour les mêmes raisons (éviter le N+1)."""
        period_start_dt = datetime.combine(date_start, time.min)
        period_end_dt = datetime.combine(date_end, time.max)
        return (
            db.query(Card)
            .options(
                joinedload(Card.list),
                joinedload(Card.labels),
                joinedload(Card.members),
            )
            .filter(Card.board_id == board_id)
            .filter(Card.started_at.isnot(None))
            .filter(Card.started_at <= period_end_dt)
            .filter((Card.completed_at.is_(None)) | (Card.completed_at >= period_start_dt))
            .order_by(Card.started_at)
            .all()
        )

    def _card_active_days_in_annual(self, card: Card, day_filter: set, period_end: date) -> set:
        """Équivalent de ExcelReportService._card_active_days_in, mais tolère
        completed_at=None (carte encore en cours en fin de période — décision produit :
        on l'inclut, cf. échange projet) en la considérant active jusqu'à la borne de fin
        de la période demandée (ou aujourd'hui si plus tôt, pour ne jamais compter de jours
        futurs sur une carte toujours ouverte)."""
        if not card.started_at:
            return set()
        start = card.started_at.date()
        end = card.completed_at.date() if card.completed_at else min(period_end, date.today())
        if end < start:
            return set()
        days = set()
        current = start
        while current <= end:
            if current in day_filter:
                days.add(current)
            current += timedelta(days=1)
        return days

    def _fetch_and_clean_cards_by_board(
        self, db: Session, boards: TypingList[Board], date_start: date, date_end: date,
    ) -> Dict[Board, TypingList[Card]]:
        """Fetch + pipeline d'exclusion complet, PAR BOARD (pas fusionné) — nécessaire pour
        tracer quel(s) board(s) alimentent chaque projet dans le rapport (cf. project_boards
        dans generate_annual_report). Les objets Board (ORM) sont utilisés comme clés : hash
        par identité, valide pour des instances chargées dans la même session."""
        period_start_dt = datetime.combine(date_start, time.min)
        period_end_dt = datetime.combine(date_end, time.max)
        result: Dict[Board, TypingList[Card]] = {}
        for board in boards:
            cards = self._fetch_cards_for_board_in_period(db, board.id, date_start, date_end)
            cards = self._excel._exclude_cards_regressed_to_retrospective(db, cards)
            cards = self._excel._exclude_cards_in_excluded_lists(cards)
            cards = self._excel._exclude_regressed_cards(cards)
            cards = self._excel._filter_cards_active_in_period(cards, period_start_dt, period_end_dt)
            result[board] = cards
        return result

    def _fetch_and_clean_cards(
        self, db: Session, boards: TypingList[Board], date_start: date, date_end: date,
    ) -> TypingList[Card]:
        """Aplatit _fetch_and_clean_cards_by_board — utilisé par list_sprints_in_period, qui
        n'a pas besoin de l'attribution par board (juste l'ensemble des sprint_number
        présents sur la période, tous boards confondus)."""
        by_board = self._fetch_and_clean_cards_by_board(db, boards, date_start, date_end)
        return [card for cards in by_board.values() for card in cards]

    def _filter_cards_by_sprints(self, cards: TypingList[Card], sprint_numbers: TypingList[int]) -> TypingList[Card]:
        """Ne garde que les cartes portant au moins un label SPRINT dont le sprint_number
        est dans sprint_numbers — même règle que le filtre SQL de _fetch_cards_for_sprints
        côté mensuel, réécrite en post-filtre Python puisque les cartes de plusieurs boards
        sont déjà fusionnées à ce stade (card.labels est déjà chargé via joinedload)."""
        wanted = set(sprint_numbers)
        return [
            card for card in cards
            if any(
                l.label_type == LabelTypeEnum.SPRINT and l.sprint_number in wanted
                for l in card.labels
            )
        ]

    def list_sprints_in_period(
        self, db: Session, date_start: date, date_end: date, board_ids: Optional[TypingList[int]] = None,
    ) -> TypingList[int]:
        """Pour l'endpoint qui alimente le sélecteur de sprints du front : la liste par
        défaut proposée à l'admin (cochée) avant qu'il ne la modifie éventuellement.

        Retourne les sprint_number triés, tous boards confondus (un même sprint_number peut
        exister sur plusieurs boards, scopés indépendamment côté labels — mais le numéro en
        tant que tel est l'unité pertinente pour l'admin, qui raisonne "sprint 34", pas
        "sprint 34 du board X")."""
        boards = self._fetch_boards(db, board_ids)
        all_cards = self._fetch_and_clean_cards(db, boards, date_start, date_end)
        sprint_numbers = {
            l.sprint_number
            for card in all_cards
            for l in card.labels
            if l.label_type == LabelTypeEnum.SPRINT and l.sprint_number is not None
        }
        return sorted(sprint_numbers)

    # ========================================================================
    # ÉCRITURE DU CLASSEUR — mise en page dédiée (pas le template Gantt mensuel)
    # ========================================================================

    def _build_workbook(
        self,
        project_dev_days: Dict[str, Dict[str, set]],
        project_boards: Dict[str, set],
        date_start: date,
        date_end: date,
        boards: TypingList[Board],
        sprint_numbers: Optional[TypingList[int]],
        working_days_count: int,
    ) -> Workbook:
        wb = Workbook()
        params_ws = wb.active
        self._write_params_sheet(params_ws, date_start, date_end, boards, sprint_numbers, working_days_count)
        overview_ws = wb.create_sheet(title="Vue d'ensemble")
        self._write_overview_sheet(overview_ws, project_dev_days, date_start, date_end, working_days_count)
        for project_name, dev_days in project_dev_days.items():
            ws = wb.create_sheet(title=self._safe_sheet_title(project_name))
            self._write_project_sheet(
                ws, project_name, dev_days, date_start, date_end,
                boards=project_boards.get(project_name, set()),
            )
        return wb

    def _write_params_sheet(
        self,
        ws,
        date_start: date,
        date_end: date,
        boards: TypingList[Board],
        sprint_numbers: Optional[TypingList[int]],
        working_days_count: int,
    ) -> None:
        """Feuille de traçabilité : sur quel périmètre exact ce fichier a été généré. Sans
        elle, rouvrir ce fichier dans 6 mois ne permet pas de savoir quels boards/sprints
        étaient inclus (l'info existe en base dans annual_report_runs, mais pas dans le
        fichier lui-même, qui peut circuler indépendamment)."""
        ws.title = "Paramètres du run"
        ws.sheet_view.showGridLines = False

        # Bandeau de titre coloré (même bleu que les en-têtes des autres feuilles), sur
        # toute la largeur utile — repère visuel immédiat que c'est la page d'accueil du
        # classeur, pas juste du texte flottant sur fond blanc.
        ws.merge_cells("A1:C1")
        band = ws["A1"]
        band.value = "  Rapport annuel TrendLabs"
        band.font = Font(name="Roboto", size=16, bold=True, color="FFFFFF")
        band.fill = HEADER_FILL
        band.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[1].height = 34
        for col in ("A", "B", "C"):
            ws[f"{col}1"].fill = HEADER_FILL

        ws.merge_cells("A2:C2")
        ws["A2"] = "Paramètres de génération de ce fichier — à consulter pour savoir sur quel périmètre exact il a été produit"
        ws["A2"].font = SUBTITLE_FONT
        ws.row_dimensions[2].height = 20

        rows = [
            ("Période", f"{date_start:%d/%m/%Y} au {date_end:%d/%m/%Y}"),
            ("Jours ouvrés de la période", f"{working_days_count} jours"),
            ("Boards inclus", ", ".join(sorted(b.name for b in boards))),
            (
                "Sprints inclus",
                ", ".join(str(n) for n in sprint_numbers) if sprint_numbers else "Aucune restriction (filtre par dates uniquement)",
            ),
            ("Généré le", datetime.now().strftime("%d/%m/%Y à %H:%M")),
        ]
        label_fill = PatternFill(start_color="EDF2FB", end_color="EDF2FB", fill_type="solid")
        row_idx = 4
        for i, (label, value) in enumerate(rows):
            label_cell = ws.cell(row=row_idx, column=1, value=label)
            label_cell.font = Font(name="Roboto", size=10, bold=True, color="1F4E78")
            label_cell.alignment = Alignment(horizontal="left", vertical="center")
            label_cell.fill = label_fill
            label_cell.border = CELL_BORDER

            value_cell = ws.cell(row=row_idx, column=2, value=value)
            ws.merge_cells(start_row=row_idx, start_column=2, end_row=row_idx, end_column=3)
            value_cell.font = CELL_FONT
            # Sprints inclus peut être une très longue liste (ex: 40 numéros) — wrap_text
            # évite qu'elle déborde sur des dizaines de colonnes invisibles à l'écran.
            value_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            value_cell.border = CELL_BORDER
            ws.cell(row=row_idx, column=3).border = CELL_BORDER
            if len(value) > 80:
                ws.row_dimensions[row_idx].height = 30
            row_idx += 1

        # ------------------------------------------------------------------
        # Légende des couleurs — explique le heatmap rouge/jaune/vert utilisé sur "Vue
        # d'ensemble" et chaque feuille projet (colonnes Total annuel / Taux d'occupation /
        # Jours travaillés), sans quoi les couleurs sont juste jolies mais pas comprises.
        # ------------------------------------------------------------------
        legend_title_row = row_idx + 2
        ws.cell(row=legend_title_row, column=1, value="Légende des couleurs").font = Font(
            name="Roboto", size=12, bold=True, color="1F4E78"
        )

        legend_rows = [
            ("F8696B", "Faible occupation", "Peu de jours actifs sur la période, relativement aux autres devs"),
            ("FFEB84", "Occupation moyenne", "Niveau d'activité intermédiaire"),
            ("63BE7B", "Forte occupation", "Beaucoup de jours actifs — proche du plafond de jours ouvrés de la période"),
        ]
        for i, (color, label, explanation) in enumerate(legend_rows):
            r = legend_title_row + 1 + i
            swatch = ws.cell(row=r, column=1)
            swatch.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
            swatch.border = CELL_BORDER
            ws.row_dimensions[r].height = 18

            label_cell = ws.cell(row=r, column=2, value=label)
            label_cell.font = Font(name="Roboto", size=10, bold=True)
            label_cell.alignment = Alignment(horizontal="left", vertical="center")

            ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)
            expl_cell = ws.cell(row=r, column=3, value=explanation)
            expl_cell.font = Font(name="Roboto", size=9, italic=True, color="6B7280")
            expl_cell.alignment = Alignment(horizontal="left", vertical="center")

        note_row = legend_title_row + 1 + len(legend_rows) + 1
        ws.cell(
            row=note_row, column=1,
            value="La couleur est relative aux valeurs de CE rapport (le dev le plus/moins actif de ce fichier), pas à un seuil fixe.",
        ).font = Font(name="Roboto", size=9, italic=True, color="9CA3AF")
        ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=5)

        ws.column_dimensions["A"].width = 26
        ws.column_dimensions["B"].width = 24
        ws.column_dimensions["C"].width = 24
        ws.column_dimensions["D"].width = 20
        ws.column_dimensions["E"].width = 20

    def _safe_sheet_title(self, name: str) -> str:
        """Excel limite les titres d'onglet à 31 caractères et interdit certains
        caractères (: \\ / ? * [ ]) — nettoyage minimal pour éviter une exception
        openpyxl si un nom de projet Trello contient l'un d'eux."""
        cleaned = "".join(c for c in name if c not in ':\\/?*[]')
        return cleaned[:31] or "Projet"

    def _write_overview_sheet(
        self, ws, project_dev_days: Dict[str, Dict[str, set]], date_start: date, date_end: date,
        working_days_count: int,
    ) -> None:
        """Matrice dev x projet. Chaque cellule projet garde une valeur NUMÉRIQUE réelle
        (le nombre de jours) avec un format d'affichage personnalisé qui ajoute '(pct%)' en
        texte à côté — pas une simple chaîne "8 (22.2%)" — pour que le heatmap couleur
        (ColorScaleRule) puisse s'appliquer sur la vraie valeur plutôt que sur du texte.
        pct = part du TOTAL ANNUEL UNIQUE de ce dev consacrée à ce projet (jours_projet /
        total_unique_dev * 100) — donc les % d'une même ligne somment à 100% SAUF si le dev a
        des jours qui se chevauchent entre 2 projets (chevauchement réel, pas un bug).
        Si vous préférez plutôt '% du total du PROJET' (jours_dev / total_jours_du_projet),
        dites-le : c'est un dénominateur différent, à changer ici uniquement."""
        ws.title = "Vue d'ensemble"
        ws.sheet_view.showGridLines = False

        all_devs = sorted({dev for days in project_dev_days.values() for dev in days})
        project_names = list(project_dev_days.keys())
        # Union des jours d'un dev, tous projets confondus — LE total annuel demandé.
        dev_unique_days = {
            dev: set().union(*(days.get(dev, set()) for days in project_dev_days.values()))
            for dev in all_devs
        }

        ws["A1"] = f"Rapport annuel — {date_start:%d/%m/%Y} au {date_end:%d/%m/%Y}"
        ws["A1"].font = TITLE_FONT
        ws["A2"] = "Jours = nombre de jours calendaires uniques où le dev a été actif sur ce projet — couleurs : voir légende dans l'onglet \"Paramètres du run\""
        ws["A2"].font = SUBTITLE_FONT

        header_row = 4
        ws.cell(row=header_row, column=1, value="Développeur").font = HEADER_FONT
        ws.cell(row=header_row, column=1).fill = HEADER_FILL
        for col_idx, project_name in enumerate(project_names, start=2):
            cell = ws.cell(row=header_row, column=col_idx, value=project_name)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = CENTER
        total_col = len(project_names) + 2
        occ_col = total_col + 1
        for col, label in ((total_col, "Total annuel (jours uniques)"), (occ_col, "Taux d'occupation")):
            cell = ws.cell(row=header_row, column=col, value=label)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = CENTER

        first_data_row = header_row + 1
        for i, dev in enumerate(all_devs):
            row_idx = first_data_row + i
            row_fill = ZEBRA_FILL if i % 2 == 1 else None

            name_cell = ws.cell(row=row_idx, column=1, value=dev)
            name_cell.font = CELL_FONT
            name_cell.alignment = LEFT
            if row_fill:
                name_cell.fill = row_fill

            dev_total = len(dev_unique_days[dev])
            for col_idx, project_name in enumerate(project_names, start=2):
                days_count = len(project_dev_days[project_name].get(dev, set()))
                cell = ws.cell(row=row_idx, column=col_idx)
                if days_count:
                    pct = round(days_count / dev_total * 100, 1) if dev_total else 0
                    cell.value = days_count
                    cell.number_format = f'0" ({pct}%)"'
                cell.alignment = CENTER
                cell.border = CELL_BORDER
                if row_fill:
                    cell.fill = row_fill

            total_cell = ws.cell(row=row_idx, column=total_col, value=dev_total)
            total_cell.font = TOTAL_ROW_FONT
            total_cell.alignment = CENTER

            occ_pct = round(dev_total / working_days_count * 100, 1) if working_days_count else 0
            occ_cell = ws.cell(row=row_idx, column=occ_col, value=occ_pct / 100)
            occ_cell.number_format = "0.0%"
            occ_cell.font = TOTAL_ROW_FONT
            occ_cell.alignment = CENTER

        last_data_row = first_data_row + len(all_devs) - 1

        # Heatmap sur les 2 colonnes de synthèse : lecture immédiate de qui est très
        # sollicité (vert) vs peu actif (rouge) sur la période, sans avoir à lire les chiffres.
        if all_devs:
            for col in (total_col, occ_col):
                col_letter = get_column_letter(col)
                ws.conditional_formatting.add(
                    f"{col_letter}{first_data_row}:{col_letter}{last_data_row}", OCCUPANCY_COLOR_SCALE,
                )

        # Ligne "Total projet" en bas de matrice : jours-personnes cumulés sur le projet (somme
        # des devs) — PAS de notion d'unicité ici, un jour où 2 devs travaillent sur le même
        # projet compte normalement pour 2 (2 personnes-jours), contrairement au total par dev.
        total_row_idx = last_data_row + 1 if all_devs else first_data_row
        ws.cell(row=total_row_idx, column=1, value="Total projet (jours-personnes)").font = TOTAL_ROW_FONT
        ws.cell(row=total_row_idx, column=1).fill = TOTAL_ROW_FILL
        for col_idx, project_name in enumerate(project_names, start=2):
            project_total = sum(len(days) for days in project_dev_days[project_name].values())
            cell = ws.cell(row=total_row_idx, column=col_idx, value=project_total)
            cell.font = TOTAL_ROW_FONT
            cell.fill = TOTAL_ROW_FILL
            cell.alignment = CENTER
        ws.cell(row=total_row_idx, column=total_col).fill = TOTAL_ROW_FILL
        ws.cell(row=total_row_idx, column=occ_col).fill = TOTAL_ROW_FILL

        ws.column_dimensions["A"].width = 28
        for col_idx in range(2, occ_col + 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = 20

        # Fige la colonne "Développeur" + les lignes d'en-tête : indispensable ici, la
        # matrice peut avoir 15+ colonnes de projets (cf. capture d'écran) et on perd le nom
        # du dev en scrollant vers la droite sans ça.
        ws.freeze_panes = ws.cell(row=first_data_row, column=2)

    def _write_project_sheet(
        self, ws, project_name: str, dev_days: Dict[str, set], date_start: date, date_end: date,
        boards: set,
    ) -> None:
        ws.sheet_view.showGridLines = False
        ws["A1"] = project_name
        ws["A1"].font = TITLE_FONT
        ws["A2"] = f"{date_start:%d/%m/%Y} — {date_end:%d/%m/%Y}"
        ws["A2"].font = SUBTITLE_FONT
        ws["A3"] = "Board(s) : " + (", ".join(sorted(boards)) if boards else "—")
        ws["A3"].font = SUBTITLE_FONT

        header_row = 5
        ws.cell(row=header_row, column=1, value="Développeur").font = HEADER_FONT
        ws.cell(row=header_row, column=1).fill = HEADER_FILL
        ws.cell(row=header_row, column=2, value="Jours travaillés").font = HEADER_FONT
        ws.cell(row=header_row, column=2).fill = HEADER_FILL

        dev_totals = {dev: len(days) for dev, days in dev_days.items()}
        sorted_devs = sorted(dev_totals.items(), key=lambda kv: kv[0])
        first_data_row = header_row + 1
        for i, (dev, total) in enumerate(sorted_devs):
            row_idx = first_data_row + i
            row_fill = ZEBRA_FILL if i % 2 == 1 else None
            name_cell = ws.cell(row=row_idx, column=1, value=dev)
            name_cell.font = CELL_FONT
            name_cell.alignment = LEFT
            cell = ws.cell(row=row_idx, column=2, value=total)
            cell.alignment = CENTER
            cell.border = CELL_BORDER
            if row_fill:
                name_cell.fill = row_fill
                cell.fill = row_fill

        if sorted_devs:
            last_data_row = first_data_row + len(sorted_devs) - 1
            ws.conditional_formatting.add(f"B{first_data_row}:B{last_data_row}", OCCUPANCY_COLOR_SCALE)
            ws.freeze_panes = ws.cell(row=first_data_row, column=1)

        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 20