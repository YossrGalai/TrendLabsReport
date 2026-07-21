import calendar
import colorsys
import logging
import os
import unicodedata
from copy import copy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List as TypingList, Optional, Tuple

from openpyxl import load_workbook
from openpyxl.styles import Alignment, PatternFill, Border, Side, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.models import Card, CardHistory, Label, LabelTypeEnum, WorkflowStageEnum

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION / CONSTANTES
# ============================================================================

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "Rapport_de_suivi_de_projet_-_Template.xlsx"

# Le docker-compose du projet monte un volume NOMMÉ persistant sur /app/storage
# (`report_files:/app/storage`), justement prévu pour stocker les fichiers générés (rapports,
# etc.) de façon durable, indépendamment du cycle de vie du conteneur. Écrire ailleurs — comme
# avant, dans un dossier à l'intérieur de l'arborescence du code source
# (Path(__file__).resolve().parent / "reports", donc /app/app/services/excel/reports) — "marche"
# en dev local UNIQUEMENT parce que ./backend:/app est aussi bind-mounté sur le host ; ça casse
# dès que l'app tourne sans ce bind-mount (ex: déploiement où seule l'image buildée + les volumes
# nommés sont utilisés) : les fichiers atterrissent alors dans la couche writable éphémère du
# conteneur et disparaissent au moindre restart/recreate → exactement le 410 "Fichier introuvable
# sur le disque" observé.
# REPORTS_STORAGE_DIR permet de surcharger ce chemin (utile en tests hors Docker) ; à défaut, on
# utilise /app/storage si ce point de montage existe (donc qu'on tourne bien dans le conteneur
# Docker du projet), sinon on retombe sur l'ancien comportement (dossier à côté de ce fichier) —
# pour ne pas casser l'exécution des tests unitaires en dehors de Docker.
_STORAGE_ROOT = Path(os.environ.get("REPORTS_STORAGE_DIR", "/app/storage"))
DEFAULT_OUTPUT_DIR = (
    _STORAGE_ROOT / "reports" if _STORAGE_ROOT.exists() else Path(__file__).resolve().parent / "reports"
)

DONE_STAGES = {
    WorkflowStageEnum.WAITING_QA,
    WorkflowStageEnum.DONE_SPRINT,
    WorkflowStageEnum.DONE_PREPROD,
    WorkflowStageEnum.IN_PROD,
}

# Tolérance (en jours calendaires) autorisée pour qu'une carte DÉBORDE du mois civil demandé et
# apparaisse quand même dans son rapport — confirmé métier : +-3/4 jours. Un simple test de
# chevauchement (started_at <= fin_mois ET completed_at >= début_mois), SANS cette borne, laissait
# entrer des cartes dont l'essentiel du travail se passait dans un tout autre mois (ex: carte
# 07/04 → 26/05 dans le rapport de MAI, alors qu'elle a démarré 24 jours avant mai ; carte
# 30/06 → 06/07 dans le rapport de JUIN, alors qu'elle finit 6 jours après juin et appartient en
# réalité au rapport de JUILLET). Avec la tolérance, started_at ET completed_at (si elle existe)
# doivent chacun tomber dans [début_mois - TOLÉRANCE, fin_mois + TOLÉRANCE] — pas juste l'un des
# deux — pour que la carte soit rattachée à CE mois-ci.
CARD_MONTH_TOLERANCE_DAYS = 4

# Stades "backlog" : si une carte a démarré (started_at renseigné, donc elle est passée par
# IN_PROGRESS à un moment) mais est repassée dans un de CES stades, c'est une régression —
# le ticket a été remis en attente, pas réellement travaillé jusqu'à aujourd'hui/fin de mois.
# Sans exclusion, faute de completed_at, le Gantt/la durée lui inventent une activité continue
# jusqu'à la fin du mois (cf. _write_gantt_marks), ce qui gonfle artificiellement les jours
# travaillés d'un dev qui n'a en réalité rien fait sur ce ticket depuis son retour en backlog.
BACKLOG_STAGES = {
    WorkflowStageEnum.PRODUCT_BACKLOG,
    WorkflowStageEnum.SPRINT_BACKLOG,
}

# Stage Trello dont les cartes ne doivent jamais apparaître dans le rapport, quel que soit
# leur sprint/label : les listes "Stand By" ET "Retrospective" pointent TOUTES LES DEUX vers
# ce même workflow_stage RETROSPECTIVE (pas de stage WAITING dédié pour "Stand By" — confirmé
# côté métier). Filtrer par stage plutôt que par nom de liste en dur est plus robuste : ça ne
# dépend pas de l'orthographe exacte du nom de liste sur Trello et suit le même principe que
# DONE_STAGES / BACKLOG_STAGES ci-dessus.
EXCLUDED_STAGES = {
    WorkflowStageEnum.RETROSPECTIVE,
}

# Labels MODULE à IGNORER lors du regroupement par catégorie (_group_by_label_type) : ce sont
# des labels de STATUT ("OK", "À clôturer") ou de méta-info ("Priorité") posés à tort comme
# MODULE sur Trello, qui produisaient des sections de rapport erronées (bandeau de section pour
# "OK" au même titre qu'un vrai module comme "Bug" ou "IA"). Une carte qui n'a plus que ces
# labels une fois filtrés retombe sur un autre label MODULE si elle en a un autre, sinon sur
# "Non classé". Comparaison normalisée (accents + casse) via _normalize_label_name, pour ne pas
# dépendre de la frappe exacte ("À clôturer" vs "A cloturer").
EXCLUDED_MODULE_LABEL_NAMES = {"priorite", "ok", "a cloturer" ,"bug" , "retour" , "retour badge" ,"test" ,"urgent" ,"demande client", "sentry" , "bug envoye sur what's up"}

UNCATEGORIZED_LABEL = "Non classé"

# Fill de la colonne "TÂCHE TERMINÉE (EN %)" — fixés en dur ici (et non plus laissés au fill
# résiduel du template) pour que la couleur soit identique sur TOUTES les lignes, y compris
# celles insérées dynamiquement par ws.insert_rows() (cf. _write_task_row).
PCT_DONE_FILL = PatternFill(start_color="61BD4F", end_color="61BD4F", fill_type="solid")
PCT_PENDING_FILL = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")

# Couleurs natives Trello (nom stocké dans Label.color) → hex, pour la coloration de ligne.
# Bien plus robuste que le matching par mot-clé : fonctionne quel que soit le nom du label,
# sur n'importe quel board, tant que le label a une couleur assignée dans Trello.
TRELLO_COLOR_HEX: Dict[str, str] = {
    "green": "61BD4F",
    "yellow": "F2D600",
    "orange": "FF9F1A",
    "red": "EB5A46",
    "purple": "C377E0",
    "blue": "0079BF",
    "sky": "00C2E0",
    "lime": "51E898",
    "pink": "FF78CB",
    "black": "4D4D4D",
    "black_light": "B3B3B3",
    "green_light": "B7DDB0",
    "yellow_light": "F5EA92",
    "orange_light": "FAD29C",
    "red_light": "EFB2B2",
    "purple_light": "DFC0EB",
    "blue_light": "A9D3F5",
    "sky_light": "BEE3E8",
    "lime_light": "C8E6C9",
    "pink_light": "F9CDDD",
}

FR_MONTHS = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]

# Colonnes fixes du template (1-indexed)
COL_NUMERO = 2          # B
COL_TITRE = 3            # C
COL_PROPRIETAIRE = 4      # D
COL_DATE_DEBUT = 5        # E
COL_DATE_FIN = 6          # F
COL_DUREE = 7             # G
COL_PCT = 8               # H
COL_GANTT_START = 9       # I
# Le Gantt est organisé par BLOCS DE SEMAINE (5 jours ouvrés = 1 semaine = 1 sprint), comme le
# template d'origine — mais celui-ci n'avait que 4 blocs (20 colonnes), donc un mois à cheval sur
# 5 ou 6 semaines calendaires était tronqué. Un mois calendaire touche au maximum 6 semaines
# ouvrées distinctes (cas d'un mois de 31 jours qui commence un samedi) : on porte donc la zone
# à 6 blocs de 5 colonnes = 30 colonnes fixes. Les blocs/colonnes non utilisés pour un mois plus
# court sont masqués (cf. _write_gantt_header). Ces colonnes supplémentaires par rapport aux 20
# d'origine sont insérées physiquement dans le classeur à l'exécution (cf. _expand_gantt_area),
# donc tout ce qui vient après (légende, colonne d'aide) se retrouve décalé d'autant.
WEEK_BLOCK_SIZE = 5
GANTT_MAX_WEEKS = 6
GANTT_MAX_COLS = WEEK_BLOCK_SIZE * GANTT_MAX_WEEKS  # 30 — capacité PHYSIQUE max de la feuille.
# Le nombre de semaines RÉELLEMENT affichées par rapport est dynamique (cf. generate_monthly_report,
# dérivé de len(sprint_numbers)) — ceci n'est que le plafond au-delà duquel la feuille elle-même
# n'a plus de colonnes disponibles (6 semaines = le maximum qu'un mois calendaire peut couvrir).
COL_GANTT_END = COL_GANTT_START + GANTT_MAX_COLS - 1  # 38 = AL
COL_CATEGORY_HELPER = 44  # AR (ex-AH/34, décalée de +10 par _expand_gantt_area) — colonne d'aide masquée

# Colonnes de la zone TOTAUX (bas de page) — DIFFÉRENTES des colonnes de la zone tâches ci-dessus.
# Le template a une mise en page dédiée pour cette section : D=Projet/Module, E=DEV, F=Total/dev,
# G=Total jours/module (nouvelle colonne, réutilise la colonne DURÉE de la zone tâches, inutilisée
# dans la zone totaux).
COL_TOTAL_PROJET = 4        # D
COL_TOTAL_DEV = 5           # E
COL_TOTAL_JOURS_DEV = 6     # F
COL_TOTAL_JOURS_MODULE = 7  # G

_THIN_SIDE = Side(style="thin", color="000000")
TOTALS_BORDER = Border(top=_THIN_SIDE, bottom=_THIN_SIDE, left=_THIN_SIDE, right=_THIN_SIDE)
TOTALS_HEADER_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
TOTALS_NO_FILL = PatternFill(fill_type=None)
TOTALS_NO_BORDER = Border()

# Zone totaux : une couleur distincte par rôle de colonne, pour que la lecture soit immédiate.
TOTALS_PROJET_FILL_HEX = "1F4E78"          # Projet/Module — bleu foncé
TOTALS_PROJET_FONT = Font(name="Roboto", size=11, bold=True, color="FFFFFF")
TOTALS_DEV_TOTAL_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")   # Total/dev — gris clair
TOTALS_PROJET_TOTAL_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")  # Bloc "TOTAL DU MOIS" — jaune

WEEKDAY_LETTERS = ["L", "M", "M", "J", "V"]  # lundi..vendredi, indexé par date.weekday()

TEMPLATE_TASK_AREA_FIRST_ROW = 11
TEMPLATE_TASK_AREA_LAST_ROW = 90   # dernière ligne "placeholder" de tâche dans le template d'origine
TEMPLATE_TOTALS_HEADER_ROW = 92    # ligne "TOTAL JOURS TRAVAILLÉS" dans le template d'origine

# Uniformisation visuelle : le template a des hauteurs de ligne héritées incohérentes
# (résidus bruts jamais nettoyés). On force donc explicitement 2 hauteurs fixes.
CATEGORY_ROW_HEIGHT = 21.0   # ligne "titre de projet"
TASK_ROW_HEIGHT = 24.0       # ligne de tâche standard — élargie (était 17.25) pour que le texte
                             # des titres de ticket (souvent sur 2 lignes) respire davantage,
                             # demande explicite suite à comparaison avec le template d'origine
TITLE_ROW_HEIGHT = 40.0      # ligne du grand titre "Récapitulatif des tâches..." (B2) — élargie
                             # sur demande explicite, la hauteur du template d'origine étant
                             # jugée trop compacte pour ce texte en gros caractères

CATEGORY_TITLE_FONT = Font(name="Roboto", size=11, bold=True, color="FFFFFF")
CATEGORY_ROW_FILL_HEX = "4472C4"  # bleu, texte blanc — remplace le gris terne du template

# Police uniforme pour TOUTES les cellules de ticket (NUMÉRO → %) : le template contient des
# résidus de police hétérogènes selon les lignes/colonnes (ex: Arial au lieu de Roboto, tailles
# manquantes) qui donnent un rendu incohérent (gras/non-gras, tailles différentes) une fois le
# rapport ouvert. On la réapplique donc explicitement à chaque ligne de ticket.
TASK_ROW_FONT = Font(name="Roboto", size=10, bold=False)

# Alignement des cellules de ticket : centré verticalement + retour à la ligne automatique,
# pour que les titres longs se lisent proprement au lieu de déborder visuellement sur la
# ligne suivante (comportement précédent, illisible avec des titres à rallonge).
TASK_ROW_ALIGNMENT = Alignment(vertical="center", wrap_text=True)

# Bordures fines du Gantt : rend la grille hebdomadaire lisible même sur les cases non
# coloriées (sans dev assigné), au lieu de cases blanches sans repère visuel.
_GANTT_SIDE = Side(style="thin", color="B7B7B7")
GANTT_BORDER = Border(left=_GANTT_SIDE, right=_GANTT_SIDE, top=_GANTT_SIDE, bottom=_GANTT_SIDE)

# Largeur minimale des colonnes de dates (E/F), suffisante pour afficher "JJ/MM/AAAA" sans
# tronquer ("###") même si la police Roboto n'est pas installée sur la machine qui ouvre le
# fichier (Excel substitue alors une police plus large que prévu par le template).
COL_DATE_DEBUT_WIDTH = 15.0
COL_DATE_FIN_WIDTH = 15.0

# Couleur de repli appliquée uniquement quand un ticket a un label secondaire MAIS que
# sa couleur Trello est absente/non reconnue (anomalie à vérifier), PAS quand le ticket
# n'a simplement aucun label secondaire (dans ce cas : blanc, c'est normal).
# Palette de couleurs par développeur (utilisée pour les cases du Gantt ET la colonne
# DEV de la zone totaux). Assignation déterministe par hash du nom → stable d'un rapport
# à l'autre sans avoir besoin de stocker un mapping externe.
DEV_COLOR_PALETTE = [
    "5B9BD5",  # bleu (Dev1 dans le template d'origine)
    "8E7CC3",  # violet (Dev2 dans le template d'origine)
    "70AD47",  # vert
    "ED7D31",  # orange
    "FFC000",  # or
    "C55A11",  # brun orangé
    "264478",  # bleu marine
    "9E480E",  # brun rouge
    "43682B",  # vert foncé
    "997300",  # olive
]

# Zone légende dédiée du template — juste après la zone Gantt (dont l'étendue physique va
# jusqu'à COL_GANTT_END, cf. plus haut), avec 1 colonne de marge. Calculée à partir de
# COL_GANTT_END plutôt qu'en dur : un ancien 'LEGEND_COL = 30' fixe tombait pile DANS la zone
# Gantt dès qu'un rapport affichait 5 semaines ou plus (colonnes Gantt jusqu'à 33), d'où le
# chevauchement visuel signalé — recalculer à partir de COL_GANTT_END (38) l'évite pour de bon.
LEGEND_COL = COL_GANTT_END + 2  # 40 = AN
LEGEND_TITLE_ROW = 26
LEGEND_MAX_ROWS = 80  # marge large pour nettoyer d'éventuels résidus d'anciens rapports

# Couleur de repli appliquée uniquement quand un ticket a un label secondaire MAIS que
# sa couleur Trello est absente/non reconnue (anomalie à vérifier), PAS quand le ticket
# n'a simplement aucun label secondaire (dans ce cas : blanc, c'est normal).
DEFAULT_ROW_FILL_HEX = "FFFFFF"

# Couleur Trello par défaut d'un label SANS couleur assignée (état normal et fréquent dans
# Trello, pas une anomalie) : Trello affiche ces labels en blanc/incolore.
NO_COLOR_LABEL_HEX = "FFFFFF"


class ExcelReportService:
    """Génère un rapport Excel mensuel à partir du template TrendLabs."""

    def __init__(self, template_path: Optional[Path] = None, output_dir: Optional[Path] = None):
        self.template_path = template_path or TEMPLATE_PATH
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Construite dynamiquement par rapport (cf. _build_dev_color_map), pour garantir une
        # couleur UNIQUE par développeur au sein d'un même rapport (pas de collision de hash).
        self._dev_color_map: Dict[str, str] = {}

    # ========================================================================
    # POINT D'ENTRÉE
    # ========================================================================

    def generate_monthly_report(
        self,
        db: Session,
        board_id: int,
        project_label_id: int,
        month: int,
        year: int,
        sprint_numbers: TypingList[int],
        chef_de_projet: str = "",
        extra_boards: Optional[TypingList[Tuple[int, Optional[int]]]] = None,
        extra_project: Optional[Tuple[int, int]] = None,
    ) -> str:
        """Génère le rapport pour UN PROJET donné (project_label_id = id interne du label
        Trello de type PROJECT) et pour les 4 SPRINTS demandés (sprint_numbers) — plus par
        mois calendaire : les cartes sont filtrées par leur label SPRINT (déjà posé sur
        Trello par l'équipe), pas par une plage de dates recalculée. `month`/`year` ne
        servent plus qu'à l'étiquetage (nom d'onglet, nom de fichier, classement du
        ReportRun) : le contenu réel du rapport dépend uniquement de sprint_numbers.
        À l'intérieur du fichier, les tickets sont regroupés par MODULE (label_type
        MODULE), plus par projet puisque le fichier n'en contient déjà qu'un seul.

        extra_boards : board(s) SUPPLÉMENTAIRE(s) à inclure en plus du board principal — ex:
        le board Transverse partagé entre tous les projets, où les cartes UI/UX (Eya Bani) et
        Integration (Oussema Chaari) portent les MÊMES 3 types de label que le board principal
        (PROJECT, SPRINT, MODULE), juste sur un board différent. Chaque carte de ces boards
        suit EXACTEMENT le même pipeline (fetch par sprint+mois, exclusions, filtre projet)
        que le board principal, puis est fusionnée dans la même liste `cards` — elle se
        retrouve alors naturellement rangée sous son label MODULE ('UI/UX', 'Integration') par
        le regroupement déjà en place (_group_by_label_type), sans code spécifique à ces 2
        catégories.

        Chaque élément est un tuple (board_id, project_label_id_override) :
        - board_id : id interne du board supplémentaire.
        - project_label_id_override : id interne du label PROJECT à utiliser SUR CE BOARD, si
          connu — les labels sont scopés PAR BOARD sur Trello (l'id du label 'Adomlingua' sur
          le board Transverse est différent de celui du board principal), donc ce n'est PAS le
          même id que project_label_id. Si None, le label est retrouvé automatiquement PAR NOM
          (même nom que project_label.name) — pratique par défaut, mais qui suppose que le nom
          du label est identique à l'octet près sur les 2 boards ; l'override permet de
          contourner ce cas quand les noms diffèrent légèrement d'un board à l'autre (cf.
          _fetch_extra_board_cards).

        extra_project : PROJET facultatif, DIFFÉRENT de project_label_id, à afficher comme UN
        SEUL MODULE supplémentaire dans CE MÊME rapport — pas fusionné carte par carte sous
        leurs propres labels MODULE comme extra_boards, mais regroupé EN BLOC sous une unique
        section portant le nom de ce projet. Cas d'usage : un projet transverse/annexe dont on
        veut suivre les tickets dans le rapport d'un autre projet, sans que ces tickets soient
        éclatés entre plusieurs modules.
        Tuple (board_id, project_label_id) :
        - board_id : id interne du board où vit ce projet — le board principal si c'est le
          même board, ou un autre board (même mécanique que extra_boards).
        - project_label_id : id interne du label PROJECT (scopé à CE board_id) dont on veut
          les cartes.
        Ces cartes suivent EXACTEMENT le même pipeline que le board principal (fetch par
        sprint+mois, exclusions, filtre projet, fenêtre du mois) puis apparaissent comme une
        section de tâches à part entière (mêmes lignes, mêmes couleurs, même Gantt) — juste
        rangées sous le nom de ce projet plutôt que sous un label MODULE individuel.

        Retourne le chemin du fichier généré."""
        project_label = db.query(Label).filter(Label.id == project_label_id).first()
        if not project_label:
            raise ValueError(f"Label projet id={project_label_id} introuvable")
        if project_label.label_type != LabelTypeEnum.PROJECT:
            raise ValueError(
                f"Le label '{project_label.name}' (id={project_label_id}) n'est pas de type PROJECT "
                f"(type actuel: {project_label.label_type})"
            )

        cards = self._fetch_cards_for_sprints(db, board_id, sprint_numbers, month, year)
        cards = self._exclude_cards_regressed_to_retrospective(db, cards)
        cards = self._exclude_cards_in_excluded_lists(cards)
        cards = self._exclude_regressed_cards(cards)
        cards = self._filter_cards_by_project(cards, project_label_id)

        for extra_board_id, extra_project_label_id in (extra_boards or []):
            cards.extend(
                self._fetch_extra_board_cards(
                    db, extra_board_id, project_label.name, sprint_numbers, month, year,
                    project_label_id_override=extra_project_label_id,
                )
            )

        # Projet facultatif affiché comme un module à part entière (cf. docstring ci-dessus) —
        # récupéré et filtré SÉPARÉMENT de `cards` (pas de .extend ici) car il doit rester
        # identifiable pour être injecté sous UN SEUL nom de section dans `categories` plus
        # bas, au lieu d'être éclaté sous les labels MODULE individuels de ses propres cartes.
        extra_project_name: Optional[str] = None
        extra_project_cards: TypingList[Card] = []
        if extra_project is not None:
            extra_project_board_id, extra_project_label_id = extra_project
            extra_project_label = (
                db.query(Label)
                .filter(Label.id == extra_project_label_id)
                .filter(Label.board_id == extra_project_board_id)
                .first()
            )
            if not extra_project_label:
                raise ValueError(
                    f"Label projet facultatif id={extra_project_label_id} introuvable sur le "
                    f"board id={extra_project_board_id}"
                )
            if extra_project_label.label_type != LabelTypeEnum.PROJECT:
                raise ValueError(
                    f"Le label '{extra_project_label.name}' (id={extra_project_label_id}) n'est "
                    f"pas de type PROJECT (type actuel: {extra_project_label.label_type})"
                )
            extra_project_name = extra_project_label.name
            extra_project_cards = self._fetch_extra_board_cards(
                db, extra_project_board_id, extra_project_name, sprint_numbers, month, year,
                project_label_id_override=extra_project_label_id,
            )

        # _fetch_cards_for_sprints inclut les cartes à cheval sur 2 mois (cf. son docstring) :
        # started_at peut donc appartenir au mois PRÉCÉDENT pour certaines cartes.
        #
        # Fenêtre d'AFFICHAGE du Gantt (colonnes/en-têtes "SEMAINE X") : demande métier — même
        # si le mois ne commence/finit pas un lundi/vendredi, on affiche quand même TOUTES les
        # semaines EN ENTIER de lundi à vendredi. Concrètement : la 1ère semaine affichée est
        # celle qui CONTIENT le 1er du mois (même si ça remonte à lundi du mois précédent), et
        # la dernière semaine affichée est celle qui CONTIENT le dernier jour du mois (même si
        # ça va jusqu'à vendredi du mois suivant). Les jours ainsi ajoutés hors mois restent de
        # simples colonnes VIDES (aucun ticket n'y est jamais marqué, cf. content_end plus bas)
        # — seulement là pour compléter visuellement le bloc de semaine.
        # Les TOTAUX (_write_totals), eux, restent strictement bornés au mois civil
        # (month_working_days ci-dessous) : ces jours de padding ne doivent jamais être comptés
        # ni pour un dev ni dans aucun total, ils sont purement visuels.
        month_start, month_end = self._month_bounds(month, year)
        period_start = month_start - timedelta(days=month_start.weekday())  # lundi de sa semaine
        period_end = month_end + timedelta(days=4 - month_end.weekday())  # vendredi de sa semaine
        period_start_dt = datetime.combine(month_start, datetime.min.time())
        period_end_dt = datetime.combine(month_end, datetime.max.time())

        # Chevauchement, pas "started_at dans la période" : une carte démarrée avant ce mois
        # mais toujours active (ou terminée) pendant le mois civil doit rester visible ici.
        # Bornée au mois civil STRICT (period_*_dt ci-dessus), pas à la fenêtre d'affichage
        # paddée : une carte qui ne chevauche que les jours de padding (hors mois) ne doit pas
        # apparaître dans ce rapport.
        cards = self._filter_cards_active_in_period(cards, period_start_dt, period_end_dt)
        extra_project_cards = self._filter_cards_active_in_period(
            extra_project_cards, period_start_dt, period_end_dt
        )
        categories = self._group_by_label_type(cards, LabelTypeEnum.MODULE)

        # Le projet facultatif est injecté APRÈS le regroupement par module normal, comme UNE
        # SEULE section supplémentaire portant son propre nom de projet — jamais éclaté sous
        # les labels MODULE de ses cartes. Ajouté en dernier (après 'Non classé' donc, déjà en
        # fin de dict grâce à _group_by_label_type) pour toujours apparaître à la fin du rapport.
        if extra_project_cards:
            categories[extra_project_name] = extra_project_cards

        # working_days = fenêtre d'AFFICHAGE paddée (cf. ci-dessus) — sert aux colonnes/barres
        # du Gantt. month_working_days = mois civil STRICT — sert UNIQUEMENT aux totaux, pour
        # qu'aucun jour de padding hors mois n'y soit jamais compté.
        working_days = self._working_days_of_month(period_start, period_end)
        month_working_days = self._working_days_of_month(month_start, month_end)
        month_weekend_days = self._weekend_days_of_period(month_start, month_end)
        self._dev_color_map = self._build_dev_color_map(categories)

        wb = load_workbook(self.template_path)
        ws = wb.active
        assert isinstance(ws, Worksheet),"Expected a valid openpyxl Worksheet"
        ws.title = self._month_label(month, year)[:31]
        self._expand_gantt_area(ws)
        self._fix_date_columns_width(ws)
        self._enlarge_task_cells(ws)
        self._unmerge_totals_block(ws)

        self._write_header(ws, project_label.name, chef_de_projet, month_start, month_end)
        month_banner_row = self._fix_static_month_label(ws, month, year)
        self._write_gantt_header(ws, working_days, cards, month_banner_row=month_banner_row)
        data_first_row, data_last_row = self._write_task_sections(ws, categories, working_days, month_end)
        self._write_totals(ws, categories, month_working_days, month_weekend_days, data_first_row, data_last_row)
        self._write_legend(ws, categories)
        self._hide_helper_column(ws)

        sprints_slug = "-".join(str(n) for n in sorted(sprint_numbers))
        output_path = (
            self.output_dir
            / f"rapport_{self._slugify(project_label.name)}_{year}_{month:02d}_sprints_{sprints_slug}.xlsx"
        )
        wb.save(output_path)
        logger.info(f"✅ Rapport généré : {output_path}")
        return str(output_path)

    # ========================================================================
    # REQUÊTE DES DONNÉES
    # ========================================================================

    def _month_bounds(self, month: int, year: int) -> Tuple[date, date]:
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        return start, end

    def _fetch_cards_for_sprints(
        self, db: Session, board_id: int, sprint_numbers: TypingList[int], month: int, year: int,
    ) -> TypingList[Card]:
        """Cartes du board ACTIVES PENDANT LE MOIS demandé (à +-CARD_MONTH_TOLERANCE_DAYS jours
        près) ET portant un label SPRINT dont le sprint_number est dans la liste demandée — les
        deux filtres sont combinés en ET.

        'Active pendant le mois' = chevauchement entre [started_at, completed_at] et le mois
        demandé ÉLARGI de CARD_MONTH_TOLERANCE_DAYS jours de chaque côté (PAS le mois civil
        strict, et surtout PAS un chevauchement sans limite comme avant) : une carte démarrée le
        30 janvier mais dont le travail réel (PR, jours actifs) se poursuit tout début février
        doit apparaître dans LES DEUX rapports — chaque rapport ne comptera de toute façon que
        les jours qui lui appartiennent (cf. _card_active_days_in, qui restreint aux jours
        ouvrés DE CE mois), donc pas de double-comptage des totaux. En revanche une carte qui
        déborde de PLUS de quelques jours d'un côté ou de l'autre SANS jamais se terminer dans
        ce mois (ex: démarrée 24 jours avant le mois et toujours active, ou terminée 6 jours
        après la fin du mois) n'a pas sa place ici : elle appartient réellement à un AUTRE mois
        et sera prise en charge par SON rapport à elle — confirmé métier, cf.
        CARD_MONTH_TOLERANCE_DAYS. Une carte encore ouverte (completed_at NULL) n'a pas de
        borne de fin à vérifier : elle reste incluse tant que son started_at est dans la
        fenêtre tolérée, cohérent avec le Gantt qui prolonge déjà sa barre jusqu'à la fin de la
        période affichée (cf. _write_gantt_marks).

        EXCEPTION à la tolérance sur started_at : si la carte est TERMINÉE pendant ce mois
        (completed_at dans la fenêtre tolérée), elle est incluse QUEL QUE SOIT son started_at,
        même démarré bien avant la fenêtre de tolérance (ex: démarrée le 2 du mois précédent,
        terminée le 8 de ce mois-ci) — confirmé métier : le rapport de ce mois doit quand même
        compter les jours travaillés depuis le 1er du mois jusqu'à sa date de fin. Sans cette
        exception, une telle carte disparaissait purement et simplement de TOUS les rapports
        (ni celui du mois précédent — completed_at pas encore atteint à ce moment-là — ni
        celui-ci — started_at hors tolérance), et sa durée de ce mois-ci n'était comptée nulle
        part. _card_active_days_in se charge ensuite de ne compter, dans les totaux, que les
        jours qui tombent réellement dans le mois civil de CE rapport (jamais ceux du mois
        précédent), donc pas de double-comptage avec le rapport précédent.

        Le filtre sprint seul ne suffit pas : une carte peut porter un label SPRINT qui
        matche (ex: réutilisé/laissé sur Trello) tout en étant active sur une tout autre
        période — sans le filtre mois, ces cartes hors période s'invitaient dans le rapport et
        gonflaient artificiellement le nombre de semaines affichées dans le Gantt (jusqu'à 7
        semaines pour un rapport censé ne couvrir qu'un mois). Le filtre mois seul ne suffit
        pas non plus : il faut bien rester filtré par sprint pour respecter le découpage métier
        (mardi→lundi sur un board, lundi→vendredi sur un autre, etc.) déjà matérialisé par
        l'équipe via le label SPRINT — d'où la combinaison des deux."""
        period_start, period_end = self._month_bounds(month, year)
        period_start_dt = datetime.combine(period_start, datetime.min.time())
        period_end_dt = datetime.combine(period_end, datetime.max.time())
        tolerance = timedelta(days=CARD_MONTH_TOLERANCE_DAYS)
        allowed_start_dt = period_start_dt - tolerance
        allowed_end_dt = period_end_dt + tolerance

        # Carte "démarrée dans la fenêtre tolérée" : comportement historique, valable pour les
        # cartes encore ouvertes (completed_at NULL) ou terminées elles aussi dans la fenêtre.
        started_in_window = (Card.started_at >= allowed_start_dt) & (Card.started_at <= allowed_end_dt)
        completed_in_window = (
            Card.completed_at.isnot(None)
            & (Card.completed_at >= allowed_start_dt)
            & (Card.completed_at <= allowed_end_dt)
        )
        # Carte "terminée dans la fenêtre tolérée" : incluse même si started_at est bien en
        # dehors de la fenêtre (cf. exception ci-dessus, point 3).
        active_in_month = or_(
            started_in_window & (Card.completed_at.is_(None) | completed_in_window),
            completed_in_window,
        )

        return (
            db.query(Card)
            .options(
                joinedload(Card.list),
                joinedload(Card.labels),
                joinedload(Card.members),
            )
            .filter(Card.board_id == board_id)
            .filter(Card.started_at.isnot(None))
            .filter(active_in_month)
            .filter(
                Card.labels.any(
                    (Label.label_type == LabelTypeEnum.SPRINT) & (Label.sprint_number.in_(sprint_numbers))
                )
            )
            .order_by(Card.started_at)
            .all()
        )

    def _period_bounds_from_cards(
        self, cards: TypingList[Card], month: int, year: int
    ) -> Tuple[date, date]:
        """Période affichée en en-tête / utilisée pour le Gantt : déduite des dates réelles
        des cartes trouvées pour les sprints demandés (min(started_at) → max(completed_at ou
        started_at)), bornée au mois civil demandé. Si aucune carte ne matche (sprints vides
        ou mal tagués), on retombe sur les bornes du mois civil pour que l'en-tête affiche
        quand même une période cohérente plutôt qu'une erreur.

        Le bornage au mois civil est nécessaire depuis que _fetch_cards_for_sprints inclut les
        cartes à cheval sur deux mois (cf. son docstring) : sans ça, une carte démarrée le 30
        janvier mais visible dans LE RAPPORT DE FÉVRIER ferait remonter le début de période
        affiché à janvier, ce qui casserait la structure '4 semaines de sprint' du Gantt de
        février. Le Gantt lui-même n'a pas besoin de ce bornage (_write_gantt_marks clippe déjà
        chaque barre aux bornes de working_days), c'est uniquement l'en-tête/la période globale
        qu'il faut garder dans le mois demandé."""
        month_start, month_end = self._month_bounds(month, year)
        if not cards:
            logger.warning(
                f"⚠️  Aucune carte trouvée pour les sprints demandés — repli sur les bornes "
                f"du mois {month:02d}/{year} pour l'affichage de la période."
            )
            return month_start, month_end

        start = max(min(card.started_at for card in cards).date(), month_start)
        end = min(max((card.completed_at or card.started_at) for card in cards).date(), month_end)
        if end < start:
            # Cas limite : toutes les cartes trouvées sont hors du mois civil sur l'axe où on
            # vient de clamper (ex: une seule carte, entièrement antérieure au mois) — on
            # retombe sur les bornes du mois plutôt que de produire une période invalide.
            return month_start, month_end
        return start, end

    def _fetch_extra_board_cards(
        self, db: Session, extra_board_id: int, project_name: str,
        sprint_numbers: TypingList[int], month: int, year: int,
        project_label_id_override: Optional[int] = None,
    ) -> TypingList[Card]:
        """Récupère les cartes d'un board SUPPLÉMENTAIRE (ex: board Transverse partagé
        UI/UX + Integration) pour le MÊME projet/sprints/mois que le board principal, en
        appliquant EXACTEMENT le même pipeline d'exclusions (régression retro, listes
        exclues, régression backlog) que le board principal — pour rester cohérent, une
        carte Transverse "en régression" ne doit pas plus apparaître qu'une carte du board
        principal dans le même état.

        Rattachement au projet, DEUX modes :
        - project_label_id_override fourni (cas normal recommandé) : on l'utilise TEL QUEL,
          après avoir vérifié qu'il pointe bien vers CE board (extra_board_id) et vers un
          label de type PROJECT — sinon 404/erreur explicite plutôt qu'un silence qui
          exclurait toutes les cartes sans prévenir. Utile quand le nom du label diffère
          légèrement entre boards (typo, casse, espace) et que la résolution par nom
          échouerait à tort.
        - project_label_id_override absent (None) : on retrouve le label PAR NOM (project_name,
          généralement project_label.name du board principal) — les labels étant scopés PAR
          BOARD sur Trello (Label.board_id), le NOM reste le seul identifiant stable entre
          boards dans ce cas."""
        if project_label_id_override is not None:
            project_label_on_extra_board = (
                db.query(Label)
                .filter(Label.id == project_label_id_override)
                .filter(Label.board_id == extra_board_id)
                .first()
            )
            if not project_label_on_extra_board:
                raise ValueError(
                    f"project_label_id={project_label_id_override} introuvable sur le board "
                    f"supplémentaire id={extra_board_id} (vérifier qu'il appartient bien à ce board)"
                )
            if project_label_on_extra_board.label_type != LabelTypeEnum.PROJECT:
                raise ValueError(
                    f"Le label '{project_label_on_extra_board.name}' (id={project_label_id_override}) "
                    f"sur le board supplémentaire id={extra_board_id} n'est pas de type PROJECT "
                    f"(type actuel: {project_label_on_extra_board.label_type})"
                )
        else:
            project_label_on_extra_board = (
                db.query(Label)
                .filter(Label.board_id == extra_board_id)
                .filter(Label.label_type == LabelTypeEnum.PROJECT)
                .filter(Label.name == project_name)
                .first()
            )
            if not project_label_on_extra_board:
                logger.info(
                    f"ℹ️ Board supplémentaire {extra_board_id} : aucun label PROJECT '{project_name}' "
                    "trouvé, aucune carte de ce board n'est incluse pour ce projet."
                )
                return []

        cards = self._fetch_cards_for_sprints(db, extra_board_id, sprint_numbers, month, year)
        cards = self._exclude_cards_regressed_to_retrospective(db, cards)
        cards = self._exclude_cards_in_excluded_lists(cards)
        cards = self._exclude_regressed_cards(cards)
        return self._filter_cards_by_project(cards, project_label_on_extra_board.id)

    def _exclude_regressed_cards(self, cards: TypingList[Card]) -> TypingList[Card]:
        """Exclut les cartes qui ont démarré (started_at renseigné, donc passées par IN_PROGRESS)
        mais qui sont revenues depuis en Product Backlog ou Sprint Backlog. Sans completed_at,
        ces cartes n'ont pas de fin réelle : les compter reviendrait à leur inventer une activité
        jusqu'à la fin du mois (cf. BACKLOG_STAGES ci-dessus), alors qu'en pratique le dev n'a
        rien fait dessus depuis ce retour en arrière."""
        kept = []
        for card in cards:
            stage = card.list.workflow_stage if card.list else None
            if stage in BACKLOG_STAGES:
                logger.info(
                    f"↩️  Carte '{card.name}' (id={card.id}) exclue du rapport : revenue en "
                    f"{stage.value} après avoir démarré le {card.started_at:%d/%m/%Y} — "
                    "régression, pas un vrai suivi de travail en cours."
                )
                continue
            kept.append(card)
        return kept

    def _exclude_cards_regressed_to_retrospective(self, db: Session, cards: TypingList[Card]) -> TypingList[Card]:
        """Exclut TOUTE carte dont l'historique (card_history) contient au moins UNE transition
        IN_PROGRESS -> RETROSPECTIVE (donc en passant par 'Stand By' ou 'Retrospective', qui
        pointent toutes les deux vers ce même stage) — QUEL QUE SOIT ce qui se passe APRÈS :
        que la carte reste dans Retrospective, ou qu'elle finisse par être déplacée vers un
        stage 'Terminé', elle ne doit JAMAIS apparaître dans le rapport. Confirmé métier : une
        fois qu'un ticket 'en cours' est mis de côté en rétro, il est considéré comme
        abandonné/reporté, même s'il est repêché et refermé plus tard — son passage ultérieur
        par un stage 'Terminé' ne suffit pas à le faire réapparaître.

        Basé sur l'HISTORIQUE, PAS sur le stage actuel de la carte : contrairement à
        _exclude_cards_in_excluded_lists (qui ne regarde que la liste ACTUELLE et raterait donc
        une carte qui a fini par sortir de Retrospective), cette exclusion reste valable même
        après que la carte ait quitté Retrospective. Une seule requête pour toutes les cartes du
        rapport (pas une par carte) pour éviter le N+1."""
        if not cards:
            return cards
        card_ids = [c.id for c in cards]
        history = (
            db.query(CardHistory)
            .filter(CardHistory.card_id.in_(card_ids))
            .filter(CardHistory.from_list_id.isnot(None))
            .options(joinedload(CardHistory.from_list), joinedload(CardHistory.to_list))
            .order_by(CardHistory.moved_at.asc())
            .all()
        )
        excluded_card_ids = set()
        for record in history:
            from_stage = record.from_list.workflow_stage if record.from_list else None
            to_stage = record.to_list.workflow_stage if record.to_list else None
            if from_stage == WorkflowStageEnum.IN_PROGRESS and to_stage == WorkflowStageEnum.RETROSPECTIVE:
                excluded_card_ids.add(record.card_id)

        kept = []
        for card in cards:
            if card.id in excluded_card_ids:
                logger.info(
                    f"🚫 Carte '{card.name}' (id={card.id}) exclue du rapport : passée de "
                    f"'En cours' à 'Retrospective/Stand By' dans son historique."
                )
                continue
            kept.append(card)
        return kept

    def _exclude_cards_in_excluded_lists(self, cards: TypingList[Card]) -> TypingList[Card]:
        """Exclut les cartes actuellement dans une liste Trello dont le workflow_stage est dans
        EXCLUDED_STAGES (ex: 'Stand By', 'Retrospective', qui pointent toutes les deux vers
        RETROSPECTIVE) — ce sont des listes hors suivi de travail réel, peu importe leur
        sprint/label/started_at, elles ne doivent jamais apparaître dans le rapport."""
        kept = []
        for card in cards:
            stage = card.list.workflow_stage if card.list else None
            if stage in EXCLUDED_STAGES:
                logger.info(
                    f"🚫 Carte '{card.name}' (id={card.id}) exclue du rapport : liste "
                    f"'{card.list.name}' (stage {stage.value})."
                )
                continue
            kept.append(card)
        return kept

    def _filter_cards_active_in_period(
        self, cards: TypingList[Card], period_start_dt: datetime, period_end_dt: datetime
    ) -> TypingList[Card]:
        """Chevauchement, pas 'started_at dans la période' : une carte démarrée avant la
        période mais toujours active (ou terminée) pendant celle-ci doit rester incluse.
        Factorisé pour être appliqué IDENTIQUEMENT aux cartes du projet principal et à celles
        du projet facultatif (extra_project) — même règle d'inclusion pour les deux."""
        return [
            card for card in cards
            if card.started_at <= period_end_dt
            and (card.completed_at is None or card.completed_at >= period_start_dt)
        ]

    def _filter_cards_by_project(self, cards: TypingList[Card], project_label_id: int) -> TypingList[Card]:
        """Ne garde que les cartes portant le label PROJECT demandé — le rapport est désormais
        généré PAR PROJET (1 fichier = 1 projet), plus par board entier (une carte peut porter
        plusieurs labels PROJECT à la fois, ex. 'Bleuevasion' + 'Maintenance' : elle apparaît
        dans le rapport de chacun des deux projets, séparément)."""
        return [card for card in cards if any(l.id == project_label_id for l in card.labels)]

    def _normalize_label_name(self, name: str) -> str:
        """Normalise un nom de label pour comparaison robuste : accents retirés, casse et espaces
        de bord ignorés (ex: 'À clôturer' et 'a cloturer' donnent tous les deux 'a cloturer')."""
        decomposed = unicodedata.normalize("NFKD", name or "")
        without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
        return without_accents.strip().lower()

    def _group_by_label_type(self, cards: TypingList[Card], label_type: LabelTypeEnum) -> Dict[str, TypingList[Card]]:
        """Groupe les cartes par TOUS les labels du type demandé qu'elles portent (pas
        seulement le 1er par id) — 'Non classé' en dernier, pour les cartes sans aucun label de
        ce type. Une carte avec 2 labels MODULE (ex: 'CRM' ET 'test') apparaît dans les DEUX
        blocs, comme un ticket travaillé sur 2 projets compte dans le total de chacun (cf.
        _write_totals) : garder un seul 'gagnant' par id faisait disparaître silencieusement
        toute catégorie secondaire dès qu'une carte avait plus d'un label MODULE — c'est ce qui
        donnait l'impression que des labels comme 'test'/'BB' n'étaient 'jamais pris en compte'
        alors que la carte existait bien, juste rangée sous l'autre label uniquement.
        Utilisée pour regrouper par MODULE à l'intérieur d'un rapport mono-projet (avant, on
        regroupait par PROJECT — mais le fichier ne contenant plus qu'un seul projet, ce
        regroupement n'aurait plus de sens).
        Les labels dont le nom (normalisé) est dans EXCLUDED_MODULE_LABEL_NAMES (ex: 'OK',
        'À clôturer', 'Priorité', 'test') sont ignorés lors du regroupement — ce sont des
        statuts/méta-infos posés à tort en tant que MODULE, pas de vrais modules. Confirmé
        métier : DEUX cas bien distincts pour une carte, selon qu'elle porte ou non un label
        exclu parmi ses labels MODULE —
        1) elle porte un label exclu ET un autre vrai label MODULE (ex: 'CRM' + 'test') → classée
           normalement sous le(s) vrai(s) module(s), le label exclu est juste ignoré ;
        2) elle porte UNIQUEMENT un ou des labels exclus comme MODULE (ex: seulement 'test') →
           elle disparaît ENTIÈREMENT du rapport, dans AUCUNE catégorie (ni sous son nom exclu,
           ni sous 'Non classé' — ce n'est pas un ticket de travail dev à comptabiliser).
        'Non classé' reste réservé au cas différent d'une carte qui n'a jamais eu AUCUN label
        MODULE du tout (aucun label exclu non plus) : elle a juste été oubliée sur Trello."""
        categories: Dict[str, TypingList[Card]] = {}
        order: TypingList[str] = []

        for card in cards:
            all_module_labels = [l for l in card.labels if l.label_type == label_type]
            matching_labels = sorted(
                [
                    l for l in all_module_labels
                    if self._normalize_label_name(l.name) not in EXCLUDED_MODULE_LABEL_NAMES
                ],
                key=lambda l: l.id,
            )
            has_excluded_label = len(matching_labels) < len(all_module_labels)

            if matching_labels:
                category_names = [l.name for l in matching_labels]
            elif has_excluded_label:
                # Seuls des labels exclus (ex: 'test') comme MODULE, aucun vrai module à côté :
                # la carte ne va nulle part, ni 'Non classé' ni sous le nom exclu.
                continue
            else:
                category_names = [UNCATEGORIZED_LABEL]

            for category_name in category_names:
                if category_name not in categories:
                    categories[category_name] = []
                    order.append(category_name)
                categories[category_name].append(card)

        if UNCATEGORIZED_LABEL in order:
            order.remove(UNCATEGORIZED_LABEL)
            order.append(UNCATEGORIZED_LABEL)

        return {name: categories[name] for name in order}

    def _working_days_of_month(self, start: date, end: date, max_days: int = GANTT_MAX_COLS) -> TypingList[date]:
        """Tous les jours ouvrés (lundi-vendredi) du mois, du 1er au dernier jour — plus de
        limite à 4 semaines. max_days reste une sécurité (23 = maximum possible sur un mois
        calendaire) pour ne jamais dépasser la largeur physique de la zone Gantt."""
        days: TypingList[date] = []
        current = start
        while current <= end and len(days) < max_days:
            if current.weekday() < 5:  # 0=lundi ... 4=vendredi
                days.append(current)
            current += timedelta(days=1)
        if current <= end:
            logger.warning(
                f"⚠️  Le mois {start:%m/%Y} contient plus de {max_days} jours ouvrés ; "
                "la grille du template n'en affiche que les premiers (limite de structure du template)."
            )
        return days

    def _weekend_days_of_period(self, start: date, end: date) -> TypingList[date]:
        """Samedis/dimanches compris entre start et end (bornes du Gantt, donc en pratique les
        week-ends « internes » aux semaines ouvrées affichées). Le Gantt lui-même n'affiche
        jamais ces jours (il reste Lundi→Vendredi, cf. demande client) — cette liste sert
        uniquement à détecter un travail exceptionnel un jour normalement chômé, pour le
        comptabiliser EN PLUS dans les totaux (cf. _write_totals)."""
        days: TypingList[date] = []
        current = start
        while current <= end:
            if current.weekday() >= 5:  # 5=samedi, 6=dimanche
                days.append(current)
            current += timedelta(days=1)
        return days

    def _month_label(self, month: int, year: int) -> str:
        return f"{FR_MONTHS[month - 1]} {year}"

    # ========================================================================
    # ÉCRITURE — EN-TÊTE
    # ========================================================================

    def _write_header(self, ws, project_name: str, chef_de_projet: str, period_start: date, period_end: date) -> None:
        ws["D4"] = project_name
        ws["P4"] = "TrendLabs"
        if chef_de_projet:
            ws["D5"] = chef_de_projet
        ws["P5"] = f"{period_start:%d/%m/%Y} - {period_end:%d/%m/%Y}"

        # Le titre (B2, zone fusionnée) contient dans le template le texte statique
        # "Récapitulatif des tâches du projet [Nom du projet]" : on y injecte le vrai nom.
        # Remplacement par sous-chaîne (et non écrasement complet de la cellule) pour rester
        # robuste si la formulation exacte du template change un peu (espaces, casse du reste
        # du texte, etc.) tant que le placeholder "[Nom du projet]" y est toujours présent.
        title_cell = ws["B2"]
        if title_cell.value and "[Nom du projet]" in str(title_cell.value):
            title_cell.value = str(title_cell.value).replace("[Nom du projet]", project_name)
        elif title_cell.value:
            logger.warning(
                "⚠️  Placeholder '[Nom du projet]' introuvable dans B2 (valeur actuelle: "
                f"'{title_cell.value}') — titre laissé tel quel, à vérifier si le template a changé."
            )

        # Hauteur de la ligne du titre (row 2, cellule B2) — demande explicite, le rendu
        # d'origine du template étant jugé trop compact pour ce texte en gros caractères.
        ws.row_dimensions[title_cell.row].height = TITLE_ROW_HEIGHT

    def _fix_static_month_label(self, ws, month: int, year: int) -> Optional[int]:
        """Le template a une bannière (fusionnée, au-dessus du bloc SEMAINE/SPRINT) affichant un
        nom de mois — jamais mis à jour depuis, contrairement à 'Période' (P5) qui, elle, est
        bien calculée dynamiquement par _write_header. On scanne la zone d'en-tête pour trouver
        cette cellule, sous DEUX formes possibles selon comment le template a été construit :

        1. Texte en dur (ex: valeur = "Juin") → on remplace directement la chaîne.
        2. Date avec un format d'affichage type "mmmm"/"mmmm yyyy" (valeur = un objet date/
           datetime, mais Excel l'affiche comme un nom de mois grâce au number_format — cas
           fréquent pour ce genre de bannière dans un template Excel pro). Un simple scan de
           chaînes ne voit RIEN ici : cell.value est un date, pas un str, donc le filtre
           `isinstance(cell.value, str)` l'ignorait silencieusement — c'est ce qui expliquait
           que la bannière restait bloquée sur le mois de création du template ('Juin') quel
           que soit le mois réellement demandé. On détecte ce cas via le number_format de la
           cellule (contient un code mois "mmm") et on remplace la date par le 1er du mois
           demandé, en conservant le format existant (donc l'affichage suit automatiquement).

        Pour une cellule fusionnée, seule la cellule en haut à gauche porte une valeur (les
        autres sont des MergedCell à None), donc ce scan la trouve sans avoir à connaître sa
        position exacte ni l'étendue de la fusion.

        Retourne le NUMÉRO DE LIGNE de la cellule trouvée (ou None si rien trouvé), pour que
        _expand_month_banner puisse cibler PRÉCISÉMENT cette ligne — et seulement celle-ci —
        plutôt que de deviner via la colonne de départ (ce qui attrapait par erreur d'autres
        cellules fusionnées de l'en-tête, ex: le bloc NOM DE L'ENTREPRISE / Période)."""
        month_names_lower = {m.lower() for m in FR_MONTHS}
        target_label = FR_MONTHS[month - 1]
        target_date = date(year, month, 1)
        found_row: Optional[int] = None

        for row in ws.iter_rows(min_row=1, max_row=12, max_col=COL_GANTT_END):
            for cell in row:
                value = cell.value
                if isinstance(value, str):
                    if value.strip().lower() in month_names_lower:
                        cell.value = target_label
                        found_row = cell.row
                elif isinstance(value, (date, datetime)):
                    number_format = (cell.number_format or "").lower()
                    if "mmm" in number_format:
                        cell.value = target_date
                        found_row = cell.row

        return found_row

    def _expand_gantt_area(self, ws) -> None:
        """Le template d'origine n'a que 20 colonnes Gantt (I → AB, 4 semaines x 5 jours).
        Pour afficher le mois entier avec ses blocs SEMAINE/SPRINT (jusqu'à 6 semaines), on
        insère les colonnes manquantes juste après l'ancienne zone Gantt, en clonant le style
        (bordures, police, remplissage) de la dernière colonne Gantt existante sur toute la
        hauteur utile de la feuille. Tout ce qui suit (légende, colonne d'aide) est
        automatiquement décalé d'autant par openpyxl — d'où les valeurs de LEGEND_COL /
        COL_CATEGORY_HELPER, calculées pour ce nouveau décalage."""
        OLD_GANTT_END = 28  # AB, position dans le template AVANT insertion (20 colonnes d'origine)
        extra = GANTT_MAX_COLS - (OLD_GANTT_END - COL_GANTT_START + 1)  # 30 - 20 = 10
        if extra <= 0:
            return
        insert_at = OLD_GANTT_END + 1
        ws.insert_cols(insert_at, amount=extra)

        model_letter = get_column_letter(OLD_GANTT_END)
        model_width = ws.column_dimensions[model_letter].width
        last_row = max(ws.max_row, TEMPLATE_TASK_AREA_LAST_ROW + 60)
        for offset in range(extra):
            col = insert_at + offset
            ws.column_dimensions[get_column_letter(col)].width = model_width
            for row in range(1, last_row + 1):
                src = ws.cell(row=row, column=OLD_GANTT_END)
                dst = ws.cell(row=row, column=col)
                dst._style = copy(src._style)

        # Les anciennes cellules "SEMAINE n" (fusionnées sur 5 colonnes) du template sont
        # démergées : le nouveau contenu par bloc de semaine/sprint est réécrit ensuite par
        # _write_gantt_header, avec des bornes de fusion recalculées dynamiquement.
        for merged_range in list(ws.merged_cells.ranges):
            if merged_range.min_row <= 9 <= merged_range.max_row and merged_range.min_col >= COL_GANTT_START:
                ws.unmerge_cells(str(merged_range))

    def _chunk_into_weeks(self, working_days: TypingList[date]) -> TypingList[TypingList[date]]:
        """Découpe la liste de jours ouvrés (déjà triée, sans trous que les week-ends) en blocs
        de semaine calendaire : une nouvelle semaine démarre dès que le jour de la semaine
        « redescend » (ex: vendredi (4) → lundi (0)). Le premier et le dernier bloc peuvent être
        partiels (mois qui ne commence/finit pas un lundi/vendredi)."""
        weeks: TypingList[TypingList[date]] = []
        current: TypingList[date] = []
        for day in working_days:
            if current and day.weekday() < current[-1].weekday():
                weeks.append(current)
                current = []
            current.append(day)
        if current:
            weeks.append(current)
        return weeks

    def _week_sprint_numbers(self, weeks: TypingList[TypingList[date]], cards: TypingList[Card]) -> TypingList[TypingList[int]]:
        """Détermine le(s) numéro(s) de sprint à afficher pour chaque bloc de semaine : TOUS
        les sprint_number rencontrés parmi les labels de type SPRINT des cartes dont le
        started_at tombe dans cette semaine (triés, sans doublon) — pas seulement le plus
        fréquent. Une équipe qui démarre un nouveau sprint en milieu de semaine (ex: sprint 26
        lundi-mardi, sprint 27 à partir de mercredi) fait alors apparaître les deux : libellé
        combiné 'SEMAINE n - SPRINT 26/27' sur le bloc entier (semaine non découpée en
        sous-blocs — confirmé métier, on ne cherche pas à isoler quel jour exact appartient à
        quel sprint, juste à signaler visuellement le chevauchement). Liste vide si aucune carte
        de la semaine n'a de label SPRINT (ou si la semaine ne contient aucune carte) → le bloc
        reste sans numéro de sprint (juste 'SEMAINE n')."""
        result: TypingList[TypingList[int]] = []
        for days in weeks:
            day_set = set(days)
            sprint_numbers_this_week: set = set()
            for card in cards:
                if not card.started_at or card.started_at.date() not in day_set:
                    continue
                for label in card.labels:
                    if label.label_type == LabelTypeEnum.SPRINT and label.sprint_number is not None:
                        sprint_numbers_this_week.add(label.sprint_number)
            result.append(sorted(sprint_numbers_this_week))
        return result

    def _write_gantt_header(
        self, ws, working_days: TypingList[date], cards: TypingList[Card], month_banner_row: Optional[int] = None,
    ) -> None:
        """Écrit l'en-tête du Gantt par blocs de semaine : une cellule fusionnée par semaine en
        ligne 9 portant "SEMAINE n" (+ "- SPRINT xx" si déterminable, cf. _week_sprint_numbers),
        et la lettre du jour (L/M/M/J/V) par colonne en ligne 10. Remplace l'ancienne limite à
        4 blocs fixes : le mois est désormais couvert en entier, quel que soit le nombre réel de
        semaines qu'il touche (jusqu'à 6). Les colonnes au-delà de la dernière semaine réelle du
        mois sont masquées. Le bandeau coloré affichant le nom du mois (repéré par
        _fix_static_month_label, ligne transmise via month_banner_row) est ensuite étendu
        d'autant (cf. _expand_month_banner) : sans ça, il restait bloqué sur la largeur d'origine
        du template (4 semaines) et un mois à 5 ou 6 semaines se retrouvait avec les dernières
        semaines sans couleur de bandeau."""
        weeks = self._chunk_into_weeks(working_days)
        week_sprint_numbers = self._week_sprint_numbers(weeks, cards)

        col = COL_GANTT_START
        for week_index, (days, sprint_numbers) in enumerate(zip(weeks, week_sprint_numbers), start=1):
            start_col = col
            for day in days:
                weekday_cell = ws.cell(row=10, column=col)
                weekday_cell.value = WEEKDAY_LETTERS[day.weekday()]
                ws.column_dimensions[get_column_letter(col)].hidden = False
                col += 1
            end_col = col - 1

            label = f"SEMAINE {week_index}"
            if sprint_numbers:
                # Libellé combiné (ex: 'SPRINT 26/27') si la semaine chevauche plusieurs
                # sprints — cf. docstring de _week_sprint_numbers.
                label += " - SPRINT " + "/".join(str(n) for n in sprint_numbers)
            header_cell = ws.cell(row=9, column=start_col, value=label)
            if end_col > start_col:
                ws.merge_cells(start_row=9, start_column=start_col, end_row=9, end_column=end_col)
            header_cell.alignment = Alignment(horizontal="center", vertical="center")

        # Colonnes au-delà de la dernière semaine réelle : masquées (mois plus court que 6 semaines)
        for unused_col in range(col, COL_GANTT_END + 1):
            ws.cell(row=9, column=unused_col).value = None
            ws.cell(row=10, column=unused_col).value = None
            ws.column_dimensions[get_column_letter(unused_col)].hidden = True

        if month_banner_row is not None:
            self._expand_month_banner(ws, last_gantt_col=col - 1, banner_row=month_banner_row)

    def _expand_month_banner(self, ws, last_gantt_col: int, banner_row: int) -> None:
        """Le bandeau coloré affichant le nom du mois (au-dessus du bloc SEMAINE/SPRINT) est un
        héritage du template Excel, fusionné sur SA largeur D'ORIGINE de 4 semaines (20
        colonnes) — cette fusion n'est jamais retouchée par _expand_gantt_area (qui ne démerge
        que les cellules de la ligne SEMAINE elle-même, cf. son commentaire) ni par
        _fix_static_month_label (qui ne fait que remplacer le TEXTE/la date affichés, pas la
        largeur de la fusion). Résultat pour un mois à 5 ou 6 semaines : le bandeau s'arrête net
        après la semaine 4, laissant les semaines suivantes sans continuité de couleur avec le
        reste de l'en-tête — confirmé métier : le bandeau doit couvrir la largeur RÉELLEMENT
        utilisée par le mois (jusqu'à last_gantt_col), pas une largeur fixe.

        banner_row est la ligne EXACTE trouvée par _fix_static_month_label (contenu = nom du
        mois) : on ne touche QUE la fusion qui commence sur CETTE ligne précise, en colonne
        COL_GANTT_START. Un précédent essai matchait toute fusion démarrant en colonne
        COL_GANTT_START sur n'importe quelle ligne < 9, ce qui attrapait par erreur d'AUTRES
        cellules fusionnées de l'en-tête (ex: le bloc NOM DE L'ENTREPRISE / Période) et les
        fusionnait de force jusqu'à last_gantt_col — effaçant leur contenu au passage (une
        fusion Excel ne conserve que la valeur de la cellule en haut à gauche). D'où le bug :
        NOM DE L'ENTREPRISE / Période vidés dès qu'un rapport dépassait 4 semaines."""
        for merged_range in list(ws.merged_cells.ranges):
            if merged_range.min_row != banner_row or merged_range.min_col != COL_GANTT_START:
                continue
            if merged_range.max_col >= last_gantt_col:
                continue  # déjà assez large (mois de 4 semaines ou moins)

            old_max_col = merged_range.max_col
            min_row, max_row = merged_range.min_row, merged_range.max_row

            for row in range(min_row, max_row + 1):
                model_cell = ws.cell(row=row, column=old_max_col)
                for extra_col in range(old_max_col + 1, last_gantt_col + 1):
                    ws.cell(row=row, column=extra_col)._style = copy(model_cell._style)

            ws.unmerge_cells(str(merged_range))
            ws.merge_cells(
                start_row=min_row, start_column=COL_GANTT_START,
                end_row=max_row, end_column=last_gantt_col,
            )

    # ========================================================================
    # ÉCRITURE — SECTIONS DE TÂCHES
    # ========================================================================

    def _write_task_sections(
        self, ws, categories: Dict[str, TypingList[Card]], working_days: TypingList[date], content_end: date
    ) -> Tuple[int, int]:
        rows_needed = sum(1 + len(cards) for cards in categories.values())
        available = TEMPLATE_TASK_AREA_LAST_ROW - TEMPLATE_TASK_AREA_FIRST_ROW + 1

        if rows_needed > available:
            extra = rows_needed - available
            insert_at = TEMPLATE_TASK_AREA_LAST_ROW + 1
            ws.insert_rows(insert_at, amount=extra)
            self._copy_row_style(ws, model_row=TEMPLATE_TASK_AREA_FIRST_ROW + 1, start_row=insert_at, count=extra)

        row = TEMPLATE_TASK_AREA_FIRST_ROW
        data_first_row = row

        for cat_index, (category_name, cards) in enumerate(categories.items(), start=1):
            self._clear_row(ws, row)
            category_fill = PatternFill(start_color=CATEGORY_ROW_FILL_HEX, end_color=CATEGORY_ROW_FILL_HEX, fill_type="solid")
            for col in range(COL_NUMERO, COL_GANTT_END + 1):
                ws.cell(row=row, column=col).fill = category_fill
            ws.cell(row=row, column=COL_NUMERO, value=float(cat_index)).font = CATEGORY_TITLE_FONT
            ws.cell(row=row, column=COL_TITRE, value=category_name).font = CATEGORY_TITLE_FONT
            ws.row_dimensions[row].height = CATEGORY_ROW_HEIGHT
            row += 1

            for task_index, card in enumerate(cards, start=1):
                self._clear_row(ws, row)
                self._write_task_row(ws, row, f"{cat_index}.{task_index}", card, category_name, working_days, content_end)
                ws.row_dimensions[row].height = TASK_ROW_HEIGHT
                row += 1

        data_last_row = row - 1

        # Nettoyage des lignes placeholder du template non utilisées (s'il en reste)
        leftover_last_row = max(TEMPLATE_TASK_AREA_LAST_ROW, data_last_row)
        for blank_row in range(row, leftover_last_row + 1):
            self._clear_row(ws, blank_row)
            ws.row_dimensions[blank_row].height = TASK_ROW_HEIGHT

        return data_first_row, data_last_row

    def _copy_row_style(self, ws, model_row: int, start_row: int, count: int) -> None:
        for offset in range(count):
            target_row = start_row + offset
            for col in range(COL_NUMERO, COL_GANTT_END + 1):
                src = ws.cell(row=model_row, column=col)
                dst = ws.cell(row=target_row, column=col)
                dst._style = copy(src._style)
            # insert_rows() ne copie pas ws.row_dimensions : sans ceci, les lignes
            # insérées gardent la hauteur par défaut d'openpyxl (incohérente avec le reste).
            ws.row_dimensions[target_row].height = TASK_ROW_HEIGHT

    def _clear_row(self, ws, row: int) -> None:
        """Vide une ligne ET réinitialise son formatage numérique.
        Le template contient des formats hérités incohérents selon les lignes/colonnes
        (ex: currency sur des colonnes Gantt, pourcentage sur d'autres, date sur NUMÉRO) —
        sans ce reset, ces formats parasites restent visibles (ex: '###', '100%' inattendu)."""
        no_fill = PatternFill(fill_type=None)
        for col in range(COL_NUMERO, COL_GANTT_END + 1):
            cell = ws.cell(row=row, column=col)
            cell.value = None
            cell.fill = no_fill
            cell.number_format = "General"
            # Réinitialise la police à une base uniforme ; les lignes "titre de projet"
            # ré-appliquent ensuite explicitement CATEGORY_TITLE_FONT par-dessus (cf.
            # _write_task_sections), donc ceci ne s'applique en pratique qu'aux lignes de tâches.
            cell.font = TASK_ROW_FONT
            cell.alignment = TASK_ROW_ALIGNMENT
        ws.cell(row=row, column=COL_CATEGORY_HELPER).value = None

    def _write_task_row(
        self, ws, row: int, numero_str: str, card: Card, category_name: str, working_days: TypingList[date],
        content_end: date,
    ) -> None:
        ws.cell(row=row, column=COL_NUMERO, value=numero_str).number_format = "@"
        ws.cell(row=row, column=COL_TITRE, value=card.name)
        ws.cell(row=row, column=COL_PROPRIETAIRE, value=self._owner_display(card))

        date_debut_cell = ws.cell(row=row, column=COL_DATE_DEBUT)
        date_fin_cell = ws.cell(row=row, column=COL_DATE_FIN)
        if card.started_at:
            date_debut_cell.value = card.started_at.date()
            date_debut_cell.number_format = "DD/MM/YYYY"
        if card.completed_at:
            date_fin_cell.value = card.completed_at.date()
            date_fin_cell.number_format = "DD/MM/YYYY"

        duree_cell = ws.cell(row=row, column=COL_DUREE)
        if card.started_at and card.completed_at:
            # Sur demande de l'encadrant : durée en jours ouvrés (et non plus en h/min).
            # Valeur numérique pure (plus de suffixe "j" collé au texte) + alignement centré,
            # pour que la colonne s'aligne proprement avec l'en-tête "DURÉE (Jour)".
            duree_cell.value = int(self._card_working_days(card))
            duree_cell.number_format = "0"
            duree_cell.alignment = Alignment(horizontal="center", vertical="center")

        pct_cell = ws.cell(row=row, column=COL_PCT)
        # 100% UNIQUEMENT si la carte est en prod (IN_PROD) — confirmé métier (rectifié) :
        # "Terminé & Validé (préprod)" NE compte PAS comme terminé, malgré son nom. Tant que la
        # carte n'est pas passée en prod, la case reste jaune SANS AUCUNE VALEUR affichée (pas
        # de 0% ni de valeur intermédiaire type 50% — juste vide, cf. branche else ci-dessous).
        is_completed_stage = bool(
            card.list
            and card.list.workflow_stage == WorkflowStageEnum.IN_PROD
        )
        if is_completed_stage:
            pct_cell.value = 1.0
            pct_cell.number_format = "0%"
            pct_cell.alignment = Alignment(horizontal="center", vertical="center")
            # Fill VERT fixé explicitement — auparavant absent ici, la case ne semblait verte
            # que parce que la ligne du template avait déjà ce fill pré-rempli en dur. Sur une
            # ligne insérée dynamiquement (ws.insert_rows(), dès qu'il y a plus de tickets que
            # de lignes prévues dans le template), openpyxl ne copie PAS le style de la ligne :
            # la case restait donc blanche/sans fill malgré un 100% bien calculé — d'où le
            # mélange vert/blanc observé selon la position de la ligne dans le tableau.
            pct_cell.fill = PCT_DONE_FILL
        else:
            pct_cell.value = None
            pct_cell.number_format = "General"
            pct_cell.fill = PCT_PENDING_FILL

        self._write_gantt_marks(ws, row, card, working_days, content_end)
        self._apply_gantt_borders(ws, row)
        #self._apply_row_color(ws, row, card)

        # Colonne d'aide masquée, utilisée par les SUMIFS de la section totaux
        ws.cell(row=row, column=COL_CATEGORY_HELPER, value=category_name)

    def _owner_display(self, card: Card) -> str:
        names = [m.full_name or m.username for m in card.members if (m.full_name or m.username)]
        return " / ".join(names)

    def _build_dev_color_map(self, categories: Dict[str, TypingList[Card]]) -> Dict[str, str]:
        """Construit une couleur UNIQUE par développeur, pour CE rapport.
        L'ancienne approche (hash du nom % taille de palette) provoquait des collisions dès
        qu'il y avait plus de développeurs que de couleurs dans DEV_COLOR_PALETTE (10) : deux
        devs différents se retrouvaient avec exactement la même couleur. Ici, on énumère
        d'abord tous les devs réellement présents dans le rapport, puis on leur attribue un
        index unique dans une palette étendue à la demande (couleurs supplémentaires générées
        par répartition uniforme de teintes HSV) si besoin — jamais de doublon."""
        dev_names = sorted({
            name for cards in categories.values() for card in cards for name in self._owner_list(card)
        })
        palette = list(DEV_COLOR_PALETTE)
        missing = len(dev_names) - len(palette)
        if missing > 0:
            for i in range(missing):
                hue = i / missing
                r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.85)
                palette.append(f"{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}")
        return {name: palette[i] for i, name in enumerate(dev_names)}

    def _dev_color(self, dev_name: str) -> str:
        """Couleur attribuée à ce développeur pour CE rapport (cf. _build_dev_color_map),
        garantie unique parmi tous les développeurs du rapport."""
        if not dev_name or dev_name == "(non assigné)":
            return DEFAULT_ROW_FILL_HEX
        return self._dev_color_map.get(dev_name, DEFAULT_ROW_FILL_HEX)

    def _working_days_between(self, start: date, end: date) -> int:
        """Nombre de jours OUVRÉS (lundi-vendredi) entre deux dates incluses.
        Remplace DAYS360 (jours calendaires 30/360, ne correspond ni aux jours ouvrés réels
        ni à ce qui est affiché dans le Gantt) par un calcul cohérent avec le reste du rapport."""
        if end < start:
            start, end = end, start
        days = 0
        current = start
        while current <= end:
            if current.weekday() < 5:
                days += 1
            current += timedelta(days=1)
        return days

    def _format_duration(self, total_seconds: int) -> str:
        """Formate une durée en secondes en 'Xh YYmin' (ex: 4h13min)."""
        if total_seconds is None or total_seconds < 0:
            return ""
        total_minutes = total_seconds // 60
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}h{minutes:02d}min"

    def _card_working_days(self, card: Card) -> float:
        if not card.started_at or not card.completed_at:
            return 0.0
        return float(self._working_days_between(card.started_at.date(), card.completed_at.date()))

    def _card_weekend_start_day(self, card: Card, weekend_day_set: set) -> set:
        """Retourne {started_at.date()} SI ce jour de démarrage tombe un week-end (samedi ou
        dimanche) ET fait partie de weekend_day_set (donc dans la période du rapport) — sinon
        un ensemble vide. Contrairement à l'ancienne règle (tous les jours de week-end
        traversés par la carte entre started_at et completed_at), on ne compte QUE le jour où
        le ticket a été démarré — cf. docstring de _write_totals. Ne dépend PAS de
        completed_at : même un ticket encore en cours doit voir son jour de démarrage
        week-end compté."""
        if not card.started_at or not weekend_day_set:
            return set()
        start_day = card.started_at.date()
        return {start_day} if start_day in weekend_day_set else set()

    def _card_active_days_in(self, card: Card, day_filter: set) -> set:
        """Jours (parmi day_filter — un ensemble de jours ouvrés OU de jours de week-end, peu
        importe, l'appelant décide) pendant lesquels la carte était active (started_at →
        completed_at), sous forme d'ensemble de dates. Utilisé pour les totaux : on veut savoir
        si un dev a travaillé un jour donné (compté une seule fois), pas additionner des durées
        de tickets — un ticket qui dure 5 jours mais chevauche un autre ticket du même dev sur 2
        de ces jours ne doit pas faire compter ces 2 jours deux fois. day_filter restreint aussi
        aux jours réellement affichés dans CE rapport : une carte démarrée le mois précédent et
        terminée ce mois-ci ne doit compter que les jours qui tombent dans la période du rapport."""
        if not card.started_at or not card.completed_at:
            return set()
        start = card.started_at.date()
        end = card.completed_at.date()
        if end < start:
            start, end = end, start
        days = set()
        current = start
        while current <= end:
            if current in day_filter:
                days.add(current)
            current += timedelta(days=1)
        return days

    def _get_card_color(self, card):
        """Utilisée par _write_gantt_marks_par_label (ancienne approche, désormais désactivée).
        Gardée disponible si vous préférez repasser le Gantt en couleur-par-label."""
        other_labels = [
            l for l in card.labels
            if l.label_type != LabelTypeEnum.PROJECT
        ]

        for label in other_labels:

            color_hex = TRELLO_COLOR_HEX.get(
                (label.color or "").lower()
            )

            if color_hex:
                return color_hex

        return None

    def _write_gantt_marks(
        self, ws, row: int, card: Card, working_days: TypingList[date], content_end: date
    ) -> None:
        if not card.started_at:
            return

        start = max(card.started_at.date(), working_days[0])

        # content_end = fin RÉELLE du mois civil (jamais working_days[-1] directement : cette
        # borne peut désormais inclure des jours de padding hors mois — cf. generate_monthly_
        # report — et une carte encore EN COURS (completed_at=None) ne doit jamais y être
        # marquée comme active, même visuellement).
        end = min(card.completed_at.date() if card.completed_at else content_end, content_end)

        # Couleur par DÉVELOPPEUR (et non par label) : premier propriétaire de la carte.
        # Une carte sans propriétaire assigné reste sans couleur dans le Gantt.
        owners = self._owner_list(card)
        color_hex = self._dev_color(owners[0]) if owners else None

        fill = None

        if color_hex:
            fill = PatternFill(
                start_color=color_hex,
                end_color=color_hex,
                fill_type="solid"
            )

        for col_offset, day in enumerate(working_days):

            if start <= day <= end:

                cell = ws.cell(
                    row=row,
                    column=COL_GANTT_START + col_offset
                )

                # soit vide
                cell.value = ""

                # soit 1 si tu veux garder les calculs éventuels
                # cell.value = 1

                cell.number_format = "General"

                if fill:
                    cell.fill = fill

    def _apply_gantt_borders(self, ws, row: int) -> None:
        """Bordures fines sur toute la grille Gantt de la ligne, même les cases sans dev
        assigné : sans ça, seules les cases coloriées ont un repère visuel et la grille
        hebdomadaire est illisible sur les tickets non affectés."""
        for col in range(COL_GANTT_START, COL_GANTT_END + 1):
            ws.cell(row=row, column=col).border = GANTT_BORDER

    def _apply_row_color(self, ws, row: int, card: Card) -> None:
        """Colore la ligne selon la couleur Trello NATIVE du 1er label non-'project' de la carte
        (via Label.color, déjà synchronisé). Remplace l'ancienne approche par mots-clés, qui ne
        fonctionnait que si les noms de labels matchaient un vocabulaire prédéfini (CRM/Sentry/...) —
        et donc ne coloriait rien sur des boards utilisant d'autres noms de labels."""
        other_labels = [l for l in card.labels if l.label_type != LabelTypeEnum.PROJECT]

        if not other_labels:
            # Vraiment aucun label secondaire : c'est un cas normal, la ligne reste blanche.
            return

        color_hex = None
        has_unrecognized_color = False
        for label in other_labels:
            raw_color = (label.color or "").strip().lower()
            if not raw_color:
                # Label SANS couleur assignée dans Trello : état normal (Trello l'affiche en
                # blanc), pas une anomalie. On ne log rien et on utilise le blanc Trello.
                continue
            resolved = TRELLO_COLOR_HEX.get(raw_color)
            if resolved:
                color_hex = resolved
                break
            has_unrecognized_color = True

        if color_hex is None:
            if has_unrecognized_color:
                # Le ticket a un label avec une valeur de couleur non vide mais absente de
                # TRELLO_COLOR_HEX (vraie anomalie à vérifier), différent du cas "sans couleur".
                label_names = ", ".join(l.name for l in other_labels)
                logger.warning(
                    f"⚠️  Carte '{card.name}' (id={card.id}) a des labels ({label_names}) "
                    "avec une couleur Trello non reconnue — à vérifier dans Trello."
                )
                color_hex = DEFAULT_ROW_FILL_HEX
            else:
                # Tous les labels secondaires sont "sans couleur" → blanc, couleur Trello par défaut.
                color_hex = NO_COLOR_LABEL_HEX

        fill = PatternFill(start_color=color_hex, end_color=color_hex, fill_type="solid")
        # Colonne % (H) exclue : sa couleur (jaune si ni IN_PROD ni DONE_PREPROD) est gérée séparément.
        for col in range(COL_NUMERO, COL_DUREE + 1):
            ws.cell(row=row, column=col).fill = fill

    # ========================================================================
    # ÉCRITURE — TOTAUX PAR DÉVELOPPEUR / CATÉGORIE
    # ========================================================================

    def _unmerge_totals_block(self, ws) -> None:
        """Démerge toute la zone à partir de la ligne de totaux du template AVANT insertion de lignes.
        openpyxl ne décale pas les plages fusionnées lors d'un insert_rows() plus haut dans la feuille,
        ce qui corrompt les cellules fusionnées existantes (ex: colonnes E/F 'DEV' fusionnées sur 2 lignes).
        On démerge donc tout ce bloc dès le départ ; les totaux seront réécrits proprement plus tard,
        avec fusion dynamique uniquement si nécessaire (cf. _write_totals)."""
        for merged_range in list(ws.merged_cells.ranges):
            if merged_range.min_row >= TEMPLATE_TOTALS_HEADER_ROW - 1:
                ws.unmerge_cells(str(merged_range))

    def _clear_totals_area(self, ws, totals_start_row: int) -> None:
        """Vide l'ancienne zone de totaux du template — valeurs ET formatage résiduel
        (fill/bordures). Le template a un style figé sur une structure fixe de 4 projets
        x 2 devs (8 lignes colorées) ; notre sortie est dynamique et peut être plus courte
        ou plus longue, donc on ne peut pas compter sur le style pré-existant du template
        (sinon des cases vides colorées/bordées traînent après le dernier total réel, ou
        inversement des lignes manquent de style si on dépasse la structure d'origine).
        Le formatage correct est réappliqué explicitement dans _write_totals().
        Le démergement a déjà été fait en amont par _unmerge_totals_block (avant insert_rows)."""
        last_row = max(ws.max_row, totals_start_row + 40)
        for row in range(totals_start_row, last_row + 1):
            for col in range(COL_NUMERO, COL_PCT + 1):
                cell = ws.cell(row=row, column=col)
                cell.value = None
                cell.fill = TOTALS_NO_FILL
                cell.border = TOTALS_NO_BORDER
                cell.number_format = "General"

    def _write_totals(
        self,
        ws,
        categories: Dict[str, TypingList[Card]],
        working_days: TypingList[date],
        weekend_days: TypingList[date],
        data_first_row: int,
        data_last_row: int,
    ) -> int:
        """Calcule et écrit les totaux JOURS TRAVAILLÉS par développeur / par projet.

        Nouvelle règle (suite retour encadrant) : on ne compte plus une durée de ticket, mais le
        nombre de jours ouvrés DISTINCTS (sur les 20-23 du mois) pendant lesquels le dev a
        travaillé sur CE projet. Concrètement, pour chaque dev et chaque projet, on prend l'UNION
        des jours ouvrés couverts par tous ses tickets de ce projet (cf. _card_active_days_in) et
        on compte la taille de cet ensemble — un jour où le dev a 2 tickets actifs sur le même
        projet ne compte qu'une fois. Un ticket multi-devs (ex. "Dev A / Dev B") crédite CHAQUE
        dev pour ces jours (pas de partage en fractions : les deux ont bien travaillé ce jour-là).
        Si un dev a travaillé sur 2 projets différents le même jour, ce jour est compté dans le
        total de CHACUN des deux projets séparément (logique métier confirmée par l'encadrant).

        Jours de week-end (samedi/dimanche) : le Gantt n'en affiche aucun (il reste
        Lundi→Vendredi), et ils ne comptent PLUS dans le total du dev — SAUF le jour de
        DÉMARRAGE du ticket (started_at) : si un ticket a été démarré un samedi ou un dimanche,
        ce jour précis est ajouté au total du dev (règle métier confirmée : on veut capter le
        fait qu'il ait démarré un ticket ce jour-là, mais pas tous les jours de week-end
        traversés ensuite pendant que le ticket restait actif). Une remarque (commentaire Excel
        sur la cellule du total) détaille alors ce jour de démarrage week-end, en plus de la
        liste des jours ouvrés NON travaillés ce mois-ci pour ce dev."""
        month_days = set(working_days)
        weekend_day_set = set(weekend_days or [])
        totals_start_row = max(TEMPLATE_TOTALS_HEADER_ROW, data_last_row + 2)
        self._clear_totals_area(ws, totals_start_row)

        # Total du mois par dev (tous modules confondus) et remarque associée (jours non
        # travaillés / jours week-end) : calculés ici en agrégeant toutes les catégories, pour
        # être écrits plus bas dans leur PROPRE bloc "TOTAL DU MOIS" (une ligne par dev).
        all_dev_days: Dict[str, set] = {}
        all_dev_weekend_days: Dict[str, set] = {}
        for category_name, cat_cards in categories.items():
            for card in cat_cards:
                owners = self._owner_list(card) or ["(non assigné)"]
                card_days = self._card_active_days_in(card, month_days)
                card_weekend_days = self._card_weekend_start_day(card, weekend_day_set)
                for owner in owners:
                    all_dev_days.setdefault(owner, set()).update(card_days)
                    if card_weekend_days:
                        all_dev_weekend_days.setdefault(owner, set()).update(card_weekend_days)

        ws.cell(row=totals_start_row, column=COL_NUMERO, value="TOTAL JOURS TRAVAILLÉS")
        ws.merge_cells(start_row=totals_start_row, start_column=COL_NUMERO, end_row=totals_start_row, end_column=COL_TITRE)
        for col in (COL_NUMERO, COL_TITRE, COL_TOTAL_PROJET, COL_TOTAL_DEV, COL_TOTAL_JOURS_DEV, COL_TOTAL_JOURS_MODULE):
            cell = ws.cell(row=totals_start_row, column=col)
            cell.fill = TOTALS_HEADER_FILL
            cell.font = Font(name="Roboto", size=11, bold=True)
        for col in (COL_TOTAL_PROJET, COL_TOTAL_DEV, COL_TOTAL_JOURS_DEV, COL_TOTAL_JOURS_MODULE):
            ws.cell(row=totals_start_row, column=col).border = TOTALS_BORDER
        ws.cell(row=totals_start_row, column=COL_TOTAL_DEV, value="DEV")
        ws.cell(row=totals_start_row, column=COL_TOTAL_JOURS_DEV, value="TOTAL DE JOUR /DEV")
        ws.cell(row=totals_start_row, column=COL_TOTAL_JOURS_MODULE, value="TOTAL JOURS /MODULE")

        row = totals_start_row + 1

        for category_name, cards in categories.items():
            dev_days: Dict[str, set] = {}
            dev_weekend_days: Dict[str, set] = {}
            for card in cards:
                owners = self._owner_list(card) or ["(non assigné)"]
                card_days = self._card_active_days_in(card, month_days)
                card_weekend_days = self._card_weekend_start_day(card, weekend_day_set)
                for owner in owners:
                    dev_days.setdefault(owner, set()).update(card_days)
                    if card_weekend_days:
                        dev_weekend_days.setdefault(owner, set()).update(card_weekend_days)

            dev_totals: Dict[str, float] = {
                dev: float(len(days) + len(dev_weekend_days.get(dev, set())))
                for dev, days in dev_days.items()
            }
            devs = sorted(dev_totals.keys()) or ["(non assigné)"]
            first_dev_row = row

            for dev in devs:
                projet_cell = ws.cell(row=row, column=COL_TOTAL_PROJET, value=category_name)
                projet_cell.fill = PatternFill(start_color=TOTALS_PROJET_FILL_HEX, end_color=TOTALS_PROJET_FILL_HEX, fill_type="solid")
                projet_cell.font = TOTALS_PROJET_FONT

                dev_cell = ws.cell(row=row, column=COL_TOTAL_DEV, value=dev)
                dev_hex = self._dev_color(dev)
                dev_cell.fill = PatternFill(start_color=dev_hex, end_color=dev_hex, fill_type="solid")
                dev_cell.font = Font(name="Roboto", size=11, bold=False)

                total_days = int(dev_totals.get(dev, 0.0))
                plural = "s" if total_days != 1 else ""
                total_cell = ws.cell(row=row, column=COL_TOTAL_JOURS_DEV, value=f"{total_days} Jour{plural}")
                total_cell.alignment = Alignment(wrap_text=True, vertical="center")
                total_cell.fill = TOTALS_DEV_TOTAL_FILL
                total_cell.font = Font(name="Roboto", size=11, bold=False)

                for col in (COL_TOTAL_PROJET, COL_TOTAL_DEV, COL_TOTAL_JOURS_DEV):
                    ws.cell(row=row, column=col).border = TOTALS_BORDER
                row += 1

            last_dev_row = row - 1
            if last_dev_row > first_dev_row:
                ws.merge_cells(start_row=first_dev_row, start_column=COL_TOTAL_PROJET, end_row=last_dev_row, end_column=COL_TOTAL_PROJET)
                # Le merge n'affecte que la cellule en haut à gauche : on ré-applique le fill
                # sur toutes les cellules démergées pour que la couleur couvre bien tout le bloc.
                for r in range(first_dev_row, last_dev_row + 1):
                    fill_cell = ws.cell(row=r, column=COL_TOTAL_PROJET)
                    fill_cell.fill = PatternFill(start_color=TOTALS_PROJET_FILL_HEX, end_color=TOTALS_PROJET_FILL_HEX, fill_type="solid")

            # Total jours/module : SOMME des totaux par dev de ce module (pas une union de
            # jours calendaires) — cohérent avec la règle déjà appliquée plus haut où un même
            # jour travaillé par 2 devs différents sur ce module compte pour CHACUN des deux,
            # donc 2 jours au total pour le module ce jour-là. Une seule cellule fusionnée sur
            # tout le bloc du module (comme la colonne Projet/Module), pas une ligne par dev.
            module_total_days = sum(int(v) for v in dev_totals.values())
            module_plural = "s" if module_total_days != 1 else ""
            module_total_cell = ws.cell(
                row=first_dev_row, column=COL_TOTAL_JOURS_MODULE, value=f"{module_total_days} Jour{module_plural}"
            )
            module_total_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            module_total_cell.font = Font(name="Roboto", size=11, bold=True)
            for r in range(first_dev_row, last_dev_row + 1):
                cell = ws.cell(row=r, column=COL_TOTAL_JOURS_MODULE)
                cell.fill = TOTALS_PROJET_TOTAL_FILL
                cell.border = TOTALS_BORDER
            if last_dev_row > first_dev_row:
                ws.merge_cells(
                    start_row=first_dev_row, start_column=COL_TOTAL_JOURS_MODULE,
                    end_row=last_dev_row, end_column=COL_TOTAL_JOURS_MODULE,
                )

        # Bloc "TOTAL DU MOIS" : une ligne PAR DEV (tous modules confondus), avec son total
        # réel de jours travaillés (déjà dédupliqués, cf. all_dev_days) et sa remarque (jours
        # non travaillés / jours week-end). C'est le seul total agrégé "tous modules" du
        # tableau. La colonne "Total jours/module" (COL_TOTAL_JOURS_MODULE) ne s'applique QU'AU
        # niveau d'un module — elle reste donc vide ici (mais bordée) pour ne pas induire une
        # confusion avec un total mensuel global qui n'a pas de sens "par module".
        month_total_devs = sorted(all_dev_days.keys())
        if month_total_devs:
            first_month_total_row = row
            for dev in month_total_devs:
                projet_cell = ws.cell(row=row, column=COL_TOTAL_PROJET, value="TOTAL DU MOIS")
                projet_cell.font = Font(name="Roboto", size=11, bold=True)
                projet_cell.fill = TOTALS_PROJET_TOTAL_FILL

                dev_cell = ws.cell(row=row, column=COL_TOTAL_DEV, value=dev)
                dev_hex = self._dev_color(dev)
                dev_cell.fill = PatternFill(start_color=dev_hex, end_color=dev_hex, fill_type="solid")
                dev_cell.font = Font(name="Roboto", size=11, bold=True)

                total_days = len(all_dev_days[dev]) + len(all_dev_weekend_days.get(dev, set()))
                remark = self._build_dev_remark(
                    dev, month_days, all_dev_days.get(dev, set()), all_dev_weekend_days.get(dev, set())
                )
                plural = "s" if total_days != 1 else ""
                cell_text = f"{total_days} Jour{plural}" + (f" ({remark})" if remark else "")
                total_cell = ws.cell(row=row, column=COL_TOTAL_JOURS_DEV, value=cell_text)
                total_cell.alignment = Alignment(wrap_text=True, vertical="center")
                total_cell.fill = TOTALS_DEV_TOTAL_FILL
                total_cell.font = Font(name="Roboto", size=11, bold=True)

                ws.cell(row=row, column=COL_TOTAL_JOURS_MODULE).border = TOTALS_BORDER
                for col in (COL_TOTAL_PROJET, COL_TOTAL_DEV, COL_TOTAL_JOURS_DEV):
                    ws.cell(row=row, column=col).border = TOTALS_BORDER
                row += 1

            last_month_total_row = row - 1
            if last_month_total_row > first_month_total_row:
                ws.merge_cells(
                    start_row=first_month_total_row, start_column=COL_TOTAL_PROJET,
                    end_row=last_month_total_row, end_column=COL_TOTAL_PROJET,
                )
                for r in range(first_month_total_row, last_month_total_row + 1):
                    ws.cell(row=r, column=COL_TOTAL_PROJET).fill = TOTALS_PROJET_TOTAL_FILL

        return row - 1

    def _build_dev_remark(
        self, dev: str, month_days: set, worked_days: set, weekend_days: set
    ) -> Optional[str]:
        """Construit le texte de la remarque affichée ENTRE PARENTHÈSES à la suite du total
        d'un dev (ex: '18 Jours (24 et 25 non travaillé)') : liste les jours ouvrés du mois où
        il n'a rien eu d'actif, et le cas échéant les jours de week-end où il a travaillé en
        plus. Retourne None si rien à signaler (dev présent tous les jours ouvrés, aucun jour
        de week-end)."""
        if dev == "(non assigné)":
            return None
        parts: TypingList[str] = []
        missing = sorted(month_days - worked_days)
        if missing:
            parts.append(f"{self._format_day_list(missing)} non travaillé")
        if weekend_days:
            plural = "s" if len(weekend_days) > 1 else ""
            parts.append(
                f"dont {len(weekend_days)} jour{plural} week-end travaillé{plural} "
                f"le {self._format_day_list(sorted(weekend_days))}"
            )
        return " ; ".join(parts) if parts else None

    def _format_day_list(self, days: TypingList[date]) -> str:
        """Formate une liste de dates en 'JJ, JJ et JJ' (juste le quantième, ex: '24 et 25'),
        ou 'JJ/MM, JJ/MM et JJ/MM' si les dates couvrent plus d'un mois calendaire (ambiguïté
        sinon sur ce qu'un simple numéro de jour désigne)."""
        if not days:
            return ""
        same_month = len({(d.month, d.year) for d in days}) == 1
        labels = [str(d.day) for d in days] if same_month else [f"{d.day:02d}/{d.month:02d}" for d in days]
        if len(labels) == 1:
            return labels[0]
        return ", ".join(labels[:-1]) + " et " + labels[-1]

    def _write_legend(self, ws, categories: Dict[str, TypingList[Card]]) -> None:
        """Écrit la légende dans la zone dédiée du template (colonne AD / 30, panneau latéral).
        Le template livre cette zone avec des exemples statiques ('Sentry', 'DEVOPS', ...) :
        on la nettoie d'abord, puis on la reconstruit entièrement à partir des labels et des
        développeurs réellement présents dans CE rapport (donc dynamique, dépendante du rapport)."""

        col_letter = get_column_letter(LEGEND_COL)
        ws.column_dimensions[col_letter].width = 25

        # 1) Nettoyage de la zone (efface les résidus d'exemples du template ou d'un rapport précédent)
        no_fill = PatternFill(fill_type=None)
        for r in range(LEGEND_TITLE_ROW, LEGEND_TITLE_ROW + LEGEND_MAX_ROWS):
            cell = ws.cell(row=r, column=LEGEND_COL)
            cell.value = None
            cell.fill = no_fill
            cell.font = Font(name="Roboto", size=10)
            cell.border = Border()

        # 2) Construction de la légende des LABELS réellement utilisés ce mois-ci
        # On exclut PROJECT (label du projet, déjà dans l'en-tête) ET MODULE (label utilisé pour
        # regrouper les tickets en catégories : il est déjà affiché en toutes lettres comme bandeau
        # de section dans le tableau principal — le remettre ici ferait doublon avec ce bandeau).
        label_legend: Dict[str, str] = {}

        # 3) Construction de la légende des DÉVELOPPEURS réellement présents ce mois-ci
        dev_names = sorted({
            name for cards in categories.values() for card in cards for name in self._owner_list(card)
        })

        row = LEGEND_TITLE_ROW
        title_cell = ws.cell(row=row, column=LEGEND_COL, value="LÉGENDE")
        title_cell.font = Font(name="Roboto", size=11, bold=True, color="FFFFFF")
        title_cell.fill = PatternFill(start_color="666666", end_color="666666", fill_type="solid")
        row += 1

        for name, color in sorted(label_legend.items()):
            cell = ws.cell(row=row, column=LEGEND_COL, value=name)
            cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
            cell.font = Font(name="Roboto", size=10)
            row += 1

        if dev_names:
            dev_header_cell = ws.cell(row=row, column=LEGEND_COL, value="Développeur")
            dev_header_cell.font = Font(name="Roboto", size=10, bold=True)
            dev_header_cell.fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
            row += 1

            for name in dev_names:
                cell = ws.cell(row=row, column=LEGEND_COL, value=name)
                dev_hex = self._dev_color(name)
                cell.fill = PatternFill(start_color=dev_hex, end_color=dev_hex, fill_type="solid")
                cell.font = Font(name="Roboto", size=10)
                row += 1

    def _owner_list(self, card: Card) -> TypingList[str]:
        return [m.full_name or m.username for m in card.members if (m.full_name or m.username)]

    # ========================================================================
    # DIVERS
    # ========================================================================

    def _fix_date_columns_width(self, ws) -> None:
        """Élargit les colonnes DATE DÉBUT / DATE FIN pour éviter l'affichage '###' : la largeur
        du template ne suffit que si la police Roboto est installée sur la machine qui ouvre le
        fichier ; sinon Excel/LibreOffice substitue une police plus large et tronque l'affichage."""
        ws.column_dimensions[get_column_letter(COL_DATE_DEBUT)].width = COL_DATE_DEBUT_WIDTH
        ws.column_dimensions[get_column_letter(COL_DATE_FIN)].width = COL_DATE_FIN_WIDTH

    def _hide_helper_column(self, ws) -> None:
        ws.column_dimensions[get_column_letter(COL_CATEGORY_HELPER)].hidden = True

    def _enlarge_task_cells(self, ws) -> None:
        """Élargit les cases jugées trop étroites/basses par rapport au template d'origine pour
        que leur contenu texte reste lisible — demande explicite (comparaison template vs
        rapport généré) :
        - colonnes jours du Gantt (I → dernière colonne Gantt, lettres L/M/M/J/V) : toutes
          FORCÉES À LA MÊME largeur (calquée sur la 1ère colonne du Gantt, donc sur la semaine 1,
          qui sert de référence). Une première version multipliait chaque colonne par SA propre
          largeur existante : si le template avait déjà des largeurs irrégulières entre colonnes
          (certaines colonnes, ex: le mercredi des semaines 2 et 4, plus larges que les autres
          dans le template d'origine), cette irrégularité restait — juste amplifiée par le
          facteur. Fixer une largeur UNIQUE et IDENTIQUE pour toutes les colonnes jours élimine
          le problème à la racine, quelle que soit l'irrégularité d'origine du template ;
        - colonne TITRE DE TICKET : élargie d'un facteur fixe, car les titres longs (souvent sur
          2 lignes) y sont fréquemment tronqués/compressés."""
        GANTT_COL_WIDTH_FACTOR = 1.4
        TITLE_COL_WIDTH_FACTOR = 1.3
        DEFAULT_GANTT_COL_WIDTH = 2.5
        DEFAULT_TITLE_COL_WIDTH = 30.0

        reference_width = ws.column_dimensions[get_column_letter(COL_GANTT_START)].width or DEFAULT_GANTT_COL_WIDTH
        uniform_width = round(reference_width * GANTT_COL_WIDTH_FACTOR, 2)
        for col in range(COL_GANTT_START, COL_GANTT_END + 1):
            ws.column_dimensions[get_column_letter(col)].width = uniform_width

        title_letter = get_column_letter(COL_TITRE)
        current_title_width = ws.column_dimensions[title_letter].width or DEFAULT_TITLE_COL_WIDTH
        ws.column_dimensions[title_letter].width = round(current_title_width * TITLE_COL_WIDTH_FACTOR, 2)

    def _slugify(self, text: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in text).strip("_")