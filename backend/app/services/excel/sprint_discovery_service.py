"""
Détection des sprints existants pour un board + projet + mois donné — alimente la
pré-sélection du formulaire mensuel (même concept que AnnualReportService.list_sprints_in_period,
mais scopé à UN projet et UN mois calendaire au lieu d'une période libre multi-projet).

Fichier séparé plutôt qu'ajouté à AnnualReportService : cette fonctionnalité appartient au
rapport MENSUEL (elle en pré-remplit le formulaire), pas à l'annuel — les regrouper aurait
mélangé deux features sans rapport direct entre elles autre que la mécanique de fetch.
"""

import calendar
import logging
from datetime import date, datetime, time
from typing import List

from sqlalchemy.orm import Session, joinedload

from app.models.models import Card, LabelTypeEnum
from app.services.excel.excel_service import ExcelReportService

logger = logging.getLogger(__name__)


class SprintDiscoveryService:
    def __init__(self):
        self._excel = ExcelReportService()  # composition : réutilise les exclusions déjà écrites

    def list_sprints_in_month(
        self, db: Session, board_id: int, project_label_id: int, month: int, year: int,
    ) -> List[int]:
        """Sprints trouvés sur les cartes de CE projet, actives durant le mois calendaire
        donné, après le même pipeline d'exclusion que la génération réelle du rapport
        (retro/backlog/listes exclues/régressions) — pour que la suggestion corresponde
        exactement à ce qui serait effectivement inclus, pas une recherche de labels au
        sens large."""
        date_start = date(year, month, 1)
        date_end = date(year, month, calendar.monthrange(year, month)[1])
        period_start_dt = datetime.combine(date_start, time.min)
        period_end_dt = datetime.combine(date_end, time.max)

        cards = (
            db.query(Card)
            .options(joinedload(Card.list), joinedload(Card.labels), joinedload(Card.members))
            .filter(Card.board_id == board_id)
            .filter(Card.started_at.isnot(None))
            .filter(Card.started_at <= period_end_dt)
            .filter((Card.completed_at.is_(None)) | (Card.completed_at >= period_start_dt))
            .order_by(Card.started_at)
            .all()
        )
        cards = self._excel._exclude_cards_regressed_to_retrospective(db, cards)
        cards = self._excel._exclude_cards_in_excluded_lists(cards)
        cards = self._excel._exclude_regressed_cards(cards)
        cards = self._excel._filter_cards_active_in_period(cards, period_start_dt, period_end_dt)

        # Filtre anti-cartes-fantômes : une carte FERMÉE (completed_at renseigné) a une
        # fenêtre d'activité bien définie, le chevauchement de période ci-dessus suffit. Mais
        # une carte encore OUVERTE (completed_at NULL) matche ce chevauchement pour TOUS les
        # mois suivant son démarrage, indéfiniment — y compris des mois où elle est en réalité
        # oubliée, sans plus aucune activité. On exige donc, pour les cartes encore ouvertes
        # UNIQUEMENT, une activité réelle (date_last_activity Trello) DANS le mois demandé —
        # sinon un vieux sprint associé à une carte abandonnée resurgirait dans chaque
        # suggestion mensuelle future tant que la carte n'est pas fermée.
        cards = [
            c for c in cards
            if c.completed_at is not None
            or (c.date_last_activity is not None and period_start_dt <= c.date_last_activity <= period_end_dt)
        ]

        # Filtre PROJET : contrairement à l'annuel (tous projets), ici on ne veut que les
        # cartes du projet précis pour lequel le formulaire mensuel est en train d'être rempli.
        cards = [
            c for c in cards
            if any(l.id == project_label_id and l.label_type == LabelTypeEnum.PROJECT for l in c.labels)
        ]

        sprint_numbers = set()
        for c in cards:
            card_sprints = [l.sprint_number for l in c.labels if l.label_type == LabelTypeEnum.SPRINT and l.sprint_number is not None]
            if not card_sprints:
                continue
            # Une carte peut accumuler plusieurs labels SPRINT au fil du temps si elle est
            # reportée d'un sprint au suivant sans que l'ancien label soit retiré (cas réel
            # observé : carte id=2788, labels [25, 27, 29, 30, 36] alors que seul 36 est
            # pertinent pour le mois en cours). On ne retient que le PLUS RÉCENT (max) comme
            # sprint représentatif de cette carte pour la suggestion — les numéros de sprint
            # augmentant dans le temps, c'est celui qui reflète où en est réellement la carte
            # aujourd'hui. Ne modifie QUE cette suggestion, pas la génération réelle du
            # rapport (excel_service.py, qui filtre par label exact, non touché).
            relevant_sprint = max(card_sprints)
            sprint_numbers.add(relevant_sprint)
            logger.info(
                f"📌 Carte '{c.name}' (id={c.id}) → sprint retenu {relevant_sprint} "
                f"(labels bruts: {card_sprints}) | started_at={c.started_at} completed_at={c.completed_at} "
                f"date_last_activity={c.date_last_activity}"
            )

        return sorted(sprint_numbers)