import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.models import (
    ActionTypeEnum,
    Board,
    Card,
    CardHistory,
    CardLabel,
    CardMember,
    Label,
    LabelTypeEnum,
    List,
    Member,
    WorkflowStageEnum,
)
from app.services.trello.trello_service import TrelloService

logger = logging.getLogger(__name__)

PO_EXCLUDED_TRELLO_IDS: set[str] = {
    "65cc7dadeb579736842299b9",
}

# Détecte les commentaires du type "PR #539 created by Aziz Ben Kbaier" (avec ou sans lien
# Bitbucket juste après) — insensible à la casse. Volontairement large ("PR" + "#numéro" +
# "created") pour couvrir les variantes de formulation sans matcher n'importe quel commentaire
# mentionnant juste le mot "PR".
PR_CREATED_COMMENT_RE = re.compile(r"\bPR\s*#\d+\s*(?:was\s+)?created\b", re.IGNORECASE)

# Fuseau horaire de l'équipe, utilisé UNIQUEMENT pour juger de l'heure locale d'un passage de
# carte (cf. _shift_early_morning_completion) — moved_at est stocké en UTC (cf. _parse_datetime).
# À ajuster si l'équipe n'est pas basée en Tunisie.
TEAM_TIMEZONE = ZoneInfo("Africa/Tunis")

# Un passage vers un stage "terminé" avant cette heure LOCALE est considéré comme du travail de
# la VEILLE — confirmé métier : un dev oublie souvent de déplacer sa carte le soir même et la
# déplace seulement le lendemain matin en arrivant, ce qui compterait sinon une journée de
# travail en plus qui n'a en réalité pas eu lieu.
EARLY_MORNING_COMPLETION_CUTOFF_HOUR = 9

# Nombre de requêtes Trello (get_card_actions) lancées en parallèle lors du fetch de
# l'historique des cartes modifiées. Ramené de 8 à 4 après des 429 (Too Many Requests) observés
# en usage réel avec 8 workers — TrelloService gère désormais un retry avec backoff sur 429 (cf.
# trello_service.py), mais mieux vaut limiter la casse en amont plutôt que de compter uniquement
# sur les retries.
HISTORY_FETCH_MAX_WORKERS = 4

class TrelloSyncService:
    """Service de synchronisation bidirectionnelle Trello ↔ MySQL"""

    def __init__(self, trello_service: TrelloService):
        self.trello = trello_service

    # ========================================================================
    # 1. SYNCHRONISATION PRINCIPALE
    # ========================================================================

    @contextmanager
    def _step_timer(self, step_durations: Dict[str, float], step_name: str):
        """Chronomètre une étape du sync et enregistre sa durée (en secondes, arrondie au ms
        près) dans step_durations[step_name]. Permet de savoir, sync après sync, quelle étape
        redevient lente en premier — plutôt que de ne connaître que le temps total, qui ne dit
        pas OÙ chercher quand un sync recommence à traîner."""
        step_start = time.perf_counter()
        try:
            yield
        finally:
            step_durations[step_name] = round(time.perf_counter() - step_start, 3)

    def full_sync_board(self, db: Session, board_id: str) -> Dict[str, Any]:
        """Synchronisation COMPLÈTE d'un board"""
        sync_start = datetime.now(timezone.utc)
        sync_start_perf = time.perf_counter()
        step_durations: Dict[str, float] = {}

        try:
            # 1. Créer/récupérer le board
            with self._step_timer(step_durations, "board"):
                board = self._sync_board(db, board_id)
            logger.info(
                f"✅ Board synced: {board.name} (ID={board.id}) "
                f"— {step_durations['board']}s"
            )

            # 2. Synchroniser les listes
            with self._step_timer(step_durations, "lists"):
                lists_count = self._sync_lists(db, board.id, board_id)
            logger.info(f"✅ {lists_count} lists synced — {step_durations['lists']}s")

            # 3. Synchroniser les membres
            with self._step_timer(step_durations, "members"):
                members_count = self._sync_members(db, board_id)
            logger.info(f"✅ {members_count} members synced — {step_durations['members']}s")

            # 4. Synchroniser les labels
            with self._step_timer(step_durations, "labels"):
                labels_count = self._sync_labels(db, board.id, board_id)
            logger.info(f"✅ {labels_count} labels synced — {step_durations['labels']}s")

            # 5. Synchroniser les cartes + relations
            # Un seul appel API pour récupérer les cartes du board, réutilisé pour _sync_cards
            # ET _sync_card_history ci-dessous (auparavant chacune refaisait son propre appel
            # get_board_cards — un aller-retour réseau complet en double à chaque sync).
            with self._step_timer(step_durations, "cards"):
                trello_cards = self.trello.get_board_cards(board_id)
                cards_count, previous_activity_by_trello_id = self._sync_cards(
                    db, board.id, trello_cards
                )
            logger.info(f"✅ {cards_count} cards synced — {step_durations['cards']}s")

            # 5bis. Détecter les catégories-projet (carte 'Product Backlog')
            #       et reclassifier les labels correspondants
            with self._step_timer(step_durations, "project_categories"):
                project_names = self._sync_project_categories(db, board.id)
                categories_updated = self._apply_project_categories(db, board.id, project_names)
            logger.info(
                f"✅ {categories_updated} labels reclassifiés en PROJECT "
                f"— {step_durations['project_categories']}s"
            )

            # 6. Synchroniser l'historique des cartes — SEULEMENT pour les cartes dont
            #    dateLastActivity a changé depuis le dernier sync connu (previous_activity_by_
            #    trello_id, capturé dans _sync_cards AVANT écrasement). C'est le principal poste
            #    de lenteur du sync complet : un appel Trello par carte. Sur un sync où peu de
            #    cartes ont bougé, ça réduit le nombre d'appels de "toutes les cartes" à
            #    "seulement celles modifiées". C'est aussi l'étape à surveiller en priorité dans
            #    step_durations["history"] : si elle redevient lente, c'est probablement le
            #    filtrage incrémental qui ne saute plus grand-chose (beaucoup de cartes modifiées
            #    d'un coup, ou previous_activity absent en masse après une remise à zéro).
            with self._step_timer(step_durations, "history"):
                history_count = self._sync_card_history(
                    db, trello_cards, previous_activity_by_trello_id
                )
            logger.info(f"✅ {history_count} history records synced — {step_durations['history']}s")

            # 6bis. Déduire un propriétaire pour les cartes sans membre Trello assigné,
            #       à partir de l'historique (cf. _infer_owners_for_unassigned_cards)
            with self._step_timer(step_durations, "inferred_owners"):
                inferred_count = self._infer_owners_for_unassigned_cards(db, board.id)
            logger.info(
                f"✅ {inferred_count} cartes non assignées → propriétaire déduit "
                f"— {step_durations['inferred_owners']}s"
            )

            # 7. Calculer started_at / completed_at / duration_working_days
            with self._step_timer(step_durations, "card_dates"):
                dates_count = self._sync_card_dates(db, board.id)
            logger.info(f"✅ {dates_count} card dates computed — {step_durations['card_dates']}s")

            # Update last_sync_at (Timezone-aware)
            board.last_sync_at = datetime.now(timezone.utc)
            db.commit()

            total_seconds = round(time.perf_counter() - sync_start_perf, 3)
            logger.info(
                f"⏱️  Sync terminé en {total_seconds}s — détail par étape : {step_durations}"
            )

            return {
                "success": True,
                "board_id": board.id,
                "board_name": board.name,
                "counts": {
                    "lists": lists_count,
                    "members": members_count,
                    "labels": labels_count,
                    "cards": cards_count,
                    "history": history_count,
                    "inferred_owners": inferred_count,
                    "dates_computed": dates_count,
                    "project_categories_detected": len(project_names),
                },
                "sync_duration_seconds": total_seconds,
                "step_durations_seconds": step_durations,
                "timestamp": sync_start.isoformat(),
            }

        except Exception as e:
            db.rollback()
            logger.error(f"❌ Sync error: {str(e)}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "step_durations_seconds": step_durations,
                "timestamp": sync_start.isoformat(),
            }

    # ========================================================================
    # 2. SYNCHRONISATION PAR ENTITÉ
    # ========================================================================

    def _sync_board(self, db: Session, trello_board_id: str) -> Board:
        """Crée ou met à jour un board"""
        trello_board = self.trello.get_board_by_id(trello_board_id)

        board = db.query(Board).filter(
            Board.trello_id == trello_board_id
        ).first()

        if board:
            board.name = trello_board.get("name", "Unknown")
            logger.debug(f"  Updated board: {board.name}")
        else:
            board = Board(
                trello_id=trello_board_id,
                name=trello_board.get("name", "Unknown"),
                created_at=datetime.now(timezone.utc),
                last_sync_at=datetime.now(timezone.utc),
            )
            db.add(board)
            logger.debug(f"  Created board: {board.name}")

        db.commit()
        return board

    def _sync_lists(self, db: Session, board_id: int, trello_board_id: str) -> int:
        """Synchronise les listes du board"""
        trello_lists = self.trello.get_board_lists(trello_board_id)
        count = 0

        for trello_list in trello_lists:
            trello_list_id = trello_list.get("id")
            existing_list = db.query(List).filter(
                List.trello_id == trello_list_id
            ).first()

            workflow_stage = self._map_workflow_stage(trello_list.get("name", ""))
            is_archived_val = bool(trello_list.get("closed", False))

            if existing_list:
                existing_list.name = trello_list.get("name", "Unknown")
                existing_list.workflow_stage = workflow_stage
                existing_list.position = trello_list.get("pos", 0)
                existing_list.is_archived = is_archived_val
            else:
                new_list = List(
                    trello_id=trello_list_id,
                    board_id=board_id,
                    name=trello_list.get("name", "Unknown"),
                    workflow_stage=workflow_stage,
                    position=trello_list.get("pos", 0),
                    is_archived=is_archived_val,
                )
                db.add(new_list)
                count += 1

        db.commit()
        return count

    def _sync_members(self, db: Session, trello_board_id: str) -> int:
        """Synchronise les membres du board"""
        trello_members = self.trello.get_board_members(trello_board_id)
        count = 0

        for trello_member in trello_members:
            trello_member_id = trello_member.get("id")
            existing_member = db.query(Member).filter(
                Member.trello_id == trello_member_id
            ).first()

            if existing_member:
                existing_member.full_name = trello_member.get("fullName")
                existing_member.username = trello_member.get("username")
                existing_member.avatar_url = self._get_avatar_url(trello_member)
            else:
                new_member = Member(
                    trello_id=trello_member_id,
                    full_name=trello_member.get("fullName"),
                    username=trello_member.get("username"),
                    avatar_url=self._get_avatar_url(trello_member),
                    created_at=datetime.now(timezone.utc),
                )
                db.add(new_member)
                count += 1

        db.commit()
        return count

    def _sync_labels(self, db: Session, board_id: int, trello_board_id: str) -> int:
        """Synchronise les labels du board"""
        trello_labels = self.trello.get_board_labels(trello_board_id)
        count = 0

        for trello_label in trello_labels:
            trello_label_id = trello_label.get("id")
            existing_label = db.query(Label).filter(
                Label.trello_id == trello_label_id
            ).first()

            label_name = trello_label.get("name", "")
            label_type, sprint_number = self._parse_label_type(label_name)

            if existing_label:
                existing_label.name = label_name
                existing_label.color = trello_label.get("color")
                existing_label.label_type = label_type
                existing_label.sprint_number = sprint_number
            else:
                new_label = Label(
                    trello_id=trello_label_id,
                    board_id=board_id,
                    name=label_name,
                    color=trello_label.get("color"),
                    label_type=label_type,
                    sprint_number=sprint_number,
                )
                db.add(new_label)
                count += 1

        db.commit()
        return count

    def _sync_cards(
        self, db: Session, board_id: int, trello_cards: list[dict]
    ) -> Tuple[int, Dict[str, Optional[datetime]]]:
        """Synchronise les cartes + relations à partir d'une liste déjà récupérée depuis Trello
        (voir full_sync_board — évite un second appel réseau identique).

        Retourne (nombre de cartes créées, dict {trello_card_id: ancienne date_last_activity}).
        Ce second élément capture la valeur de date_last_activity AVANT qu'elle ne soit écrasée
        par la valeur fraîche ci-dessous : c'est ce qui permet à _sync_card_history de détecter
        quelles cartes ont réellement changé depuis le dernier sync (None = carte nouvelle,
        jamais synchronisée → son historique doit être fetché en entier).
        """
        count = 0
        previous_activity_by_trello_id: Dict[str, Optional[datetime]] = {}

        for trello_card in trello_cards:
            trello_card_id = trello_card.get("id")

            existing_card = db.query(Card).filter(
                Card.trello_id == trello_card_id
            ).first()

            trello_list_id = trello_card.get("idList")
            card_list = db.query(List).filter(
                List.trello_id == trello_list_id
            ).first()

            if not card_list:
                logger.warning(f"⚠️  List not found for card {trello_card_id}")
                continue

            if existing_card:
                previous_activity_by_trello_id[trello_card_id] = existing_card.date_last_activity
                existing_card.name = trello_card.get("name", "Unknown")
                existing_card.description = trello_card.get("desc", "")
                existing_card.due_date = self._parse_datetime(trello_card.get("due"))
                existing_card.due_complete = bool(trello_card.get("dueComplete", False))
                existing_card.date_last_activity = self._parse_datetime(
                    trello_card.get("dateLastActivity")
                )
                existing_card.is_closed = bool(trello_card.get("closed", False))
                existing_card.list_id = card_list.id
                existing_card.board_id = board_id
            else:
                previous_activity_by_trello_id[trello_card_id] = None
                new_card = Card(
                    trello_id=trello_card_id,
                    board_id=board_id,
                    list_id=card_list.id,
                    name=trello_card.get("name", "Unknown"),
                    description=trello_card.get("desc", ""),
                    due_date=self._parse_datetime(trello_card.get("due")),
                    due_complete=bool(trello_card.get("dueComplete", False)),
                    date_last_activity=self._parse_datetime(
                        trello_card.get("dateLastActivity")
                    ),
                    is_closed=bool(trello_card.get("closed", False)),
                    created_at=datetime.now(timezone.utc),
                )
                db.add(new_card)
                db.flush()
                existing_card = new_card
                count += 1

            if existing_card and getattr(existing_card, 'id', None) is not None:
                self._sync_card_members(
                    db, existing_card.id, trello_card.get("idMembers", [])
                )
                self._sync_card_labels(
                    db, existing_card.id, trello_card.get("idLabels", [])
                )

        db.commit()
        return count, previous_activity_by_trello_id

    def _sync_card_members(self, db: Session, card_id: int, trello_member_ids: list[str]) -> None:
        """Synchronise les membres assignés à une carte"""
        current_members = db.query(CardMember).filter(
            CardMember.card_id == card_id
        ).all()
        current_member_ids = {cm.member_id for cm in current_members}

        trello_member_objects = db.query(Member).filter(
            Member.trello_id.in_(trello_member_ids)
        ).all()
        trello_member_db_ids = {m.id for m in trello_member_objects}

        for member_id in current_member_ids - trello_member_db_ids:
            db.query(CardMember).filter(
                CardMember.card_id == card_id,
                CardMember.member_id == member_id
            ).delete()

        for member_id in trello_member_db_ids - current_member_ids:
            new_card_member = CardMember(
                card_id=card_id,
                member_id=member_id
            )
            db.add(new_card_member)

        # Pas de commit ici : appelée depuis _sync_cards() dans une boucle par carte, un commit
        # par carte multiplierait inutilement les allers-retours DB. Le commit unique de
        # _sync_cards() à la fin de sa boucle couvre déjà ces changements.

    def _sync_card_labels(self, db: Session, card_id: int, trello_label_ids: list[str]) -> None:
        """Synchronise les labels assignés à une carte"""
        current_labels = db.query(CardLabel).filter(
            CardLabel.card_id == card_id
        ).all()
        current_label_ids = {cl.label_id for cl in current_labels}

        trello_label_objects = db.query(Label).filter(
            Label.trello_id.in_(trello_label_ids)
        ).all()
        trello_label_db_ids = {l.id for l in trello_label_objects}

        for label_id in current_label_ids - trello_label_db_ids:
            db.query(CardLabel).filter(
                CardLabel.card_id == card_id,
                CardLabel.label_id == label_id
            ).delete()

        for label_id in trello_label_db_ids - current_label_ids:
            new_card_label = CardLabel(
                card_id=card_id,
                label_id=label_id
            )
            db.add(new_card_label)

        # Pas de commit ici, même raison que _sync_card_members ci-dessus.

    def _fetch_card_actions_parallel(
        self, trello_card_ids: list[str]
    ) -> Dict[str, list]:
        """Récupère les actions Trello (get_card_actions) de plusieurs cartes EN PARALLÈLE.

        Ces appels sont indépendants les uns des autres (une carte = un appel HTTP), donc le
        temps total était auparavant la somme de toutes les latences réseau — avec
        HISTORY_FETCH_MAX_WORKERS workers, il se rapproche plutôt de (nombre de cartes /
        max_workers) x latence moyenne. On reste à 8 workers par prudence vis-à-vis du rate
        limit Trello (cf. commentaire sur HISTORY_FETCH_MAX_WORKERS) — si TrelloService partage
        une session HTTP unique non thread-safe entre les appels, il faudra adapter TrelloService
        pour créer une session par thread (ou utiliser un client HTTP thread-safe) avant
        d'augmenter ce nombre.

        Une carte dont l'appel échoue est absente du dict retourné (voir le log d'erreur) plutôt
        que de faire échouer tout le sync — comportement équivalent au try/except par carte
        qu'il y avait avant dans la boucle séquentielle.
        """
        actions_by_trello_id: Dict[str, list] = {}

        with ThreadPoolExecutor(max_workers=HISTORY_FETCH_MAX_WORKERS) as executor:
            future_to_card_id = {
                executor.submit(
                    self.trello.get_card_actions,
                    trello_card_id,
                    action_types="createCard,copyCard,updateCard,commentCard",
                ): trello_card_id
                for trello_card_id in trello_card_ids
            }

            for future in as_completed(future_to_card_id):
                trello_card_id = future_to_card_id[future]
                try:
                    actions_by_trello_id[trello_card_id] = future.result()
                except Exception as e:
                    logger.error(
                        f"❌ Error fetching actions for card {trello_card_id}: {e}"
                    )

        return actions_by_trello_id

    def _to_aware_utc(self, dt: Optional[datetime]) -> Optional[datetime]:
        """Normalise un datetime éventuellement naive en UTC aware.

        MySQL (colonne DATETIME) ne conserve pas le fuseau horaire : une valeur écrite en UTC
        aware (via _parse_datetime) revient NAIVE une fois relue depuis la DB, alors que
        _parse_datetime() sur une réponse Trello fraîche produit toujours un datetime AWARE
        (+00:00). Comparer directement les deux lève TypeError ("can't compare offset-naive and
        offset-aware datetimes") — d'où ce helper, à utiliser sur toute valeur qui a pu transiter
        par la DB avant d'être comparée à une valeur fraîchement parsée depuis Trello. Sûr ici
        car toutes les datetimes de ce service sont stockées en UTC (cf. _parse_datetime)."""
        if dt is None or dt.tzinfo is not None:
            return dt
        return dt.replace(tzinfo=timezone.utc)

    def _floor_to_second(self, dt: Optional[datetime]) -> Optional[datetime]:
        """Tronque un datetime à la seconde (microseconde=0).

        Purement une histoire de comparaison Python — ne touche pas la colonne DATETIME en base.
        Correspond exactement à la précision réellement stockée par une colonne MySQL DATETIME
        (qui garde les secondes, seules les millisecondes sont perdues à l'écriture) : ex.
        14:23:11.734 devient 14:23:11, ce qui correspond à ce que MySQL a de toute façon stocké.
        À utiliser pour toute comparaison entre une valeur relue depuis MySQL et une valeur
        fraîchement parsée depuis Trello (qui, elle, garde toujours ses millisecondes) — sans ce
        floor, une carte strictement inchangée ressort "différente" à chaque comparaison."""
        if dt is None:
            return None
        return dt.replace(microsecond=0)

    def _sync_card_history(
        self,
        db: Session,
        trello_cards: list[dict],
        previous_activity_by_trello_id: Dict[str, Optional[datetime]],
    ) -> int:
        """Synchronise l'historique des cartes — en ne rappelant Trello QUE pour les cartes
        dont dateLastActivity a changé depuis le dernier sync connu.

        Avant : un appel get_card_actions par carte du board, à CHAQUE sync, même si rien n'avait
        bougé — c'était le principal poste de lenteur du sync complet (un aller-retour réseau par
        carte, en série). Désormais : on compare le dateLastActivity renvoyé par Trello à
        l'ancienne valeur stockée avant ce sync (previous_activity_by_trello_id, capturé dans
        _sync_cards juste avant écrasement) — une carte identique n'a aucune raison d'avoir de
        nouvelles actions, on saute donc son appel réseau. Les cartes qui ont bougé (ou qui sont
        nouvelles → previous_activity absent/None, donc jamais fetchées) sont ensuite récupérées
        en parallèle via _fetch_card_actions_parallel plutôt qu'en séquentiel.
        """
        trello_card_ids = [c.get("id") for c in trello_cards]
        cards = db.query(Card).filter(Card.trello_id.in_(trello_card_ids)).all()
        cards_by_trello_id = {card.trello_id: card for card in cards}

        cards_to_fetch: list[Card] = []
        for trello_card in trello_cards:
            trello_card_id = trello_card.get("id")
            card = cards_by_trello_id.get(trello_card_id)
            if not card:
                continue

            trello_last_activity = self._parse_datetime(trello_card.get("dateLastActivity"))
            previous_activity = self._to_aware_utc(
                previous_activity_by_trello_id.get(trello_card_id)
            )

            # Comparaison à la SECONDE près (cf. _floor_to_second) : une colonne MySQL DATETIME
            # classique tronque silencieusement les millisecondes à l'écriture, alors que Trello
            # renvoie toujours dateLastActivity avec des millisecondes. Sans ce floor, une carte
            # totalement inchangée ressortait quand même "modifiée" à chaque sync, ce qui annulait
            # une bonne partie du gain du filtrage incrémental.
            trello_last_activity_floored = self._floor_to_second(trello_last_activity)
            previous_activity_floored = self._floor_to_second(previous_activity)

            if (
                previous_activity_floored is not None
                and trello_last_activity_floored is not None
                and trello_last_activity_floored <= previous_activity_floored
            ):
                # Rien de nouveau côté Trello sur cette carte depuis le dernier sync : on ne
                # refait pas l'appel get_card_actions pour elle.
                continue

            cards_to_fetch.append(card)

        if not cards_to_fetch:
            logger.info("✅ Aucune carte modifiée depuis le dernier sync — historique inchangé")
            return 0

        logger.info(
            f"  ↪️  {len(cards_to_fetch)}/{len(cards)} carte(s) modifiée(s) depuis le dernier "
            f"sync → fetch de leur historique (les autres sont sautées)"
        )

        actions_by_trello_id = self._fetch_card_actions_parallel(
            [card.trello_id for card in cards_to_fetch]
        )

        count = 0
        for card in cards_to_fetch:
            try:
                trello_actions = actions_by_trello_id.get(card.trello_id)
                if trello_actions is None:
                    # Le fetch parallèle a échoué pour cette carte (déjà loggé) : on la saute,
                    # elle sera retentée au prochain sync.
                    continue

                # Le dernier commentaire "PR #xxx created by ..." trouvé sur cette carte, tous
                # membres confondus (contrairement aux déplacements de liste, on ne filtre PAS
                # par propriétaire actuel ici : un PR posté par un dev qui a depuis quitté la
                # carte reste un signal de complétion valide).
                latest_pr_comment_at: Optional[datetime] = None

                for action in trello_actions:
                    action_type = action.get("type")
                    action_trello_id = action.get("id")

                    if action_type == "commentCard":
                        comment_text = (action.get("data") or {}).get("text") or ""
                        if PR_CREATED_COMMENT_RE.search(comment_text):
                            comment_at = self._parse_datetime(action.get("date"))
                            if comment_at and (latest_pr_comment_at is None or comment_at > latest_pr_comment_at):
                                latest_pr_comment_at = comment_at
                        # Les commentaires ne sont jamais persistés dans card_history : la
                        # colonne to_list_id y est NOT NULL (une conversation n'a pas de liste
                        # de destination) et on n'a pas besoin de les rejouer un par un, juste
                        # de retenir la dernière date de PR trouvée (cf. plus bas).
                        continue

                    existing_action = db.query(CardHistory).filter(
                        CardHistory.action_trello_id == action_trello_id
                    ).first()

                    if existing_action:
                        continue

                    member_creator = action.get("memberCreator") or {}
                    actor_trello_id = member_creator.get("id")
                    actor_full_name = member_creator.get("fullName")

                    if action_type == "updateCard" and "data" in action:
                        card_data = action["data"].get("card", {})
                        list_data = action["data"].get("listAfter", {})
                        list_before = action["data"].get("listBefore", {})

                        if "idList" in card_data or list_before:
                            from_list_id = None
                            to_list_id = None

                            if list_before:
                                from_list = db.query(List).filter(
                                    List.trello_id == list_before.get("id")
                                ).first()
                                if from_list:
                                    from_list_id = from_list.id

                            if list_data:
                                to_list = db.query(List).filter(
                                    List.trello_id == list_data.get("id")
                                ).first()
                                if to_list:
                                    to_list_id = to_list.id

                            if to_list_id:
                                new_history = CardHistory(
                                    card_id=card.id,
                                    from_list_id=from_list_id,
                                    to_list_id=to_list_id,
                                    moved_at=self._parse_datetime(action.get("date")),
                                    action_trello_id=action_trello_id,
                                    action_type=ActionTypeEnum.UPDATE_CARD_LIST,
                                    actor_trello_id=actor_trello_id,
                                    actor_full_name=actor_full_name,
                                )
                                db.add(new_history)
                                count += 1

                    elif action_type in ("createCard", "copyCard"):
                        to_list = db.query(List).filter(
                            List.trello_id == action["data"]["list"]["id"]
                        ).first()

                        if to_list:
                            new_history = CardHistory(
                                card_id=card.id,
                                from_list_id=None,
                                to_list_id=to_list.id,
                                moved_at=self._parse_datetime(action.get("date")),
                                action_trello_id=action_trello_id,
                                action_type=ActionTypeEnum.CREATE_CARD,
                                actor_trello_id=actor_trello_id,
                                actor_full_name=actor_full_name,
                            )
                            db.add(new_history)
                            count += 1

                if card.last_pr_comment_at != latest_pr_comment_at:
                    card.last_pr_comment_at = latest_pr_comment_at
                    count += 1

            except IntegrityError:
                db.rollback()
                logger.warning(f"⚠️  Duplicate action for card {card.trello_id}")
                continue
            except Exception as e:
                logger.error(f"❌ Error syncing history for card {card.trello_id}: {e}")
                continue

        db.commit()
        return count

    def _sync_project_categories(self, db: Session, board_id: int) -> set:
        """
        Détecte les catégories-projet valides à partir des LABELS assignés à la
        carte "Description" située dans la liste 'product_backlog' du board.

        Doit être appelé APRÈS _sync_cards() (qui synchronise aussi card_labels),
        donc cette carte et ses labels doivent déjà être en base.

        Relu à CHAQUE synchronisation : si les labels de cette carte changent sur
        Trello, la liste de catégories-projet suit automatiquement au prochain sync.
        """
        doc_card = (
            db.query(Card)
            .join(List, Card.list_id == List.id)
            .filter(Card.board_id == board_id)
            .filter(func.lower(func.replace(List.name, "_", " ")) == "product backlog")
            .filter(func.lower(Card.name) == "description")
            .first()
        )

        if not doc_card:
            logger.warning(
                "⚠️  Carte 'Description' dans la liste 'product_backlog' introuvable — "
                "aucune catégorie-projet détectée, tous les labels resteront OTHER/SPRINT"
            )
            return set()

        project_names = {label.name.strip().lower() for label in doc_card.labels}
        logger.info(f"✅ {len(project_names)} catégories-projet détectées : {sorted(project_names)}")
        return project_names

    def _apply_project_categories(self, db: Session, board_id: int, project_names: set) -> int:
        """
        Applique label_type=PROJECT à tous les labels du board dont le nom
        correspond à une catégorie détectée par _sync_project_categories().

        Si un label était PROJECT avant mais ne fait plus partie de la liste
        actuelle (retiré de la carte de doc sur Trello), il repasse en OTHER —
        cohérent avec le principe "relu à chaque sync".
        """
        if not project_names:
            return 0

        labels = db.query(Label).filter(Label.board_id == board_id).all()
        count = 0

        for label in labels:
            is_project = label.name.strip().lower() in project_names

            if is_project and label.label_type != LabelTypeEnum.PROJECT:
                label.label_type = LabelTypeEnum.PROJECT
                label.sprint_number = None
                count += 1
            elif not is_project and label.label_type == LabelTypeEnum.PROJECT:
                label.label_type = LabelTypeEnum.OTHER
                count += 1

        db.commit()
        return count

    # ========================================================================
    # 3. HELPERS - MAPPING ET PARSING
    # ========================================================================

    def _map_workflow_stage(self, list_name: str) -> WorkflowStageEnum:
        """Mappe le nom de la liste Trello → WorkflowStageEnum (robuste & multilingue)"""
        if not list_name:
            return WorkflowStageEnum.OTHER

        name_lower = list_name.lower()
        # Normaliser: remplacer underscores et espaces multiples par un seul espace, PUIS retirer
        # les accents (é/è/à/ê... -> e/e/a/e...). Le retrait d'accents rend le mapping tolérant
        # aux variantes orthographiques d'un même nom de liste renommé sur Trello au fil du temps
        # (ex: liste historique "A vérifier / QA" renommée depuis en "Done / à vérifer" — notez
        # le 'i' manquant dans 'vérifer' : sans retrait d'accent + radical court, "à vérifier"
        # ne matche PAS "à vérifer", la liste retombe alors sur OTHER par défaut et son
        # historique de mouvements devient invisible pour started_at/completed_at).
        name_normalized = re.sub(r'[\s_]+', ' ', name_lower).strip()
        decomposed = unicodedata.normalize("NFKD", name_normalized)
        name_normalized = "".join(c for c in decomposed if not unicodedata.combining(c))

        # Mapping flexible (gère l'anglais et le français du Trello)
        mapping = [
            # Product Backlog
            ("product backlog", WorkflowStageEnum.PRODUCT_BACKLOG),

            # Sprint Backlog
            ("sprint backlog", WorkflowStageEnum.SPRINT_BACKLOG),

            # In Progress / En cours
            ("in progress", WorkflowStageEnum.IN_PROGRESS),
            ("en cours", WorkflowStageEnum.IN_PROGRESS),

            # À vérifier / QA — radical "verif" (SANS accent ni terminaison) plutôt que le mot
            # complet : matche "à vérifier", "a verifier", "vérifer" (variante sans le 'i'), et
            # toute autre variante future du même mot, tant que le radical reste "verif".
            ("verif", WorkflowStageEnum.WAITING_QA),
            ("/ qa", WorkflowStageEnum.WAITING_QA),

            # Done / À valider / Waiting validation
            ("waiting validation", WorkflowStageEnum.WAITING_VALIDATION),
            ("a valider", WorkflowStageEnum.WAITING_VALIDATION),

            # Done Sprint / Fini ce sprint
            ("done sprint", WorkflowStageEnum.DONE_SPRINT),
            ("fini ce sprint", WorkflowStageEnum.DONE_SPRINT),

            # Done Preprod
            ("done preprod", WorkflowStageEnum.DONE_PREPROD),
            ("preprod", WorkflowStageEnum.DONE_PREPROD),

            # In Prod / En prod
            ("in prod", WorkflowStageEnum.IN_PROD),
            ("en prod", WorkflowStageEnum.IN_PROD),

            # Autres
            ("feedback", WorkflowStageEnum.FEEDBACK),
            ("retrospective", WorkflowStageEnum.RETROSPECTIVE),
            # "Stand By" est considérée côté métier comme une rétrospective (pas d'équivalent
            # WAITING dédié pour cette liste) — confirmé explicitement, ne pas la faire tomber
            # sur OTHER par défaut ni sur WAITING.
            ("stand by", WorkflowStageEnum.RETROSPECTIVE),
            # "Retours" est elle aussi considérée côté métier comme une rétrospective : une
            # simple pause (pas un vrai retravail après complétion) — confirmé explicitement.
            # Sans cette entrée, la liste tombait sur OTHER par défaut et son historique
            # devenait invisible pour started_at/completed_at (cf. warning "No workflow stage
            # mapped for 'Retours'").
            # Radical "retour" (sans "s") plutôt que "retours" pour matcher aussi bien "Retour"
            # que "Retours", par cohérence avec le radical "verif" plus haut.
            ("retour", WorkflowStageEnum.RETROSPECTIVE),
            ("waiting", WorkflowStageEnum.WAITING),
        ]

        for key, enum_member in mapping:
            if key in name_normalized:
                # Utilise .name (ex: SPRINT_BACKLOG) au lieu de .value pour le log, c'est plus sûr
                logger.debug(f"  ✅ Mapped list '{list_name}' → {enum_member.name}")
                return enum_member

        logger.warning(f"  ⚠️  No workflow stage mapped for '{list_name}' → defaulting to OTHER")
        return WorkflowStageEnum.OTHER

    def _parse_label_type(self, label_name: str) -> Tuple[LabelTypeEnum, Optional[int]]:
        """Parse le nom du label → type + sprint_number.

        Le fallback par défaut est MODULE (et non PROJECT) : sur ce board, les labels de
        modules sont des noms métier libres ('AI', 'Application mobile', 'GYG', 'retour',
        'demande client'...) sans mot-clé identifiable, donc ils tombent tous ici par
        élimination. Ce n'est pas un problème pour les vrais labels PROJECT : ils sont
        recorrigés juste après par _apply_project_categories() (étape 5bis de
        full_sync_board), qui force label_type=PROJECT sur les noms lus depuis la carte
        'Description' de la liste Product Backlog — indépendamment du type assigné ici.
        """
        label_lower = label_name.lower()

        if "sprint" in label_lower:
            match = re.search(r'\d+', label_name)
            sprint_num = int(match.group()) if match else None
            return LabelTypeEnum.SPRINT, sprint_num

        if "module" in label_lower:
            return LabelTypeEnum.MODULE, None
        if "status" in label_lower:
            return LabelTypeEnum.STATUS, None
        if "work" in label_lower or "type" in label_lower:
            return LabelTypeEnum.WORK_TYPE, None

        return LabelTypeEnum.MODULE, None

    def _parse_datetime(self, datetime_str: Optional[str]) -> Optional[datetime]:
        """Parse une chaîne datetime ISO 8601 (Timezone-Aware)"""
        if not datetime_str:
            return None
        try:
            return datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
        except Exception as e:
            logger.warning(f"⚠️  Could not parse datetime: {datetime_str} ({e})")
            return None

    def _get_avatar_url(self, trello_member: Dict[str, Any]) -> Optional[str]:
        """Construit l'URL de l'avatar Trello"""
        avatar_hash = trello_member.get("avatarHash")
        if avatar_hash:
            return f"https://trello-avatars.s3.amazonaws.com/{avatar_hash}/170.jpg"
        return None

    def _shift_early_morning_completion(self, moved_at: Optional[datetime]) -> Optional[datetime]:
        """Si le passage vers un stage 'terminé' a lieu tôt le matin (avant
        EARLY_MORNING_COMPLETION_CUTOFF_HOUR, en heure LOCALE de l'équipe), on considère que le
        travail a en réalité été terminé la VEILLE — confirmé métier (cf. commentaire sur
        EARLY_MORNING_COMPLETION_CUTOFF_HOUR). moved_at est stocké en UTC (cf. _parse_datetime) :
        on convertit d'abord en heure locale pour juger de l'heure, mais on décale ensuite le
        timestamp UTC d'ORIGINE de -1 jour (pas de conversion de fuseau qui resterait), pour ne
        modifier QUE la date et garder l'heure telle quelle (duration_real_seconds reste donc
        cohérent avec l'heure réelle du déplacement, seule sa date change).

        Ne s'applique QU'aux passages de stage (DONE_STAGES) — PAS à last_pr_comment_at
        (override PR juste après, cf. plus bas) : un commentaire de PR reflète un événement
        automatique horodaté au moment réel du travail, sans risque d'oubli de la part du dev,
        contrairement au déplacement manuel d'une carte sur Trello."""
        if moved_at is None:
            return moved_at
        local_time = moved_at.astimezone(TEAM_TIMEZONE)
        if local_time.hour < EARLY_MORNING_COMPLETION_CUTOFF_HOUR:
            return moved_at - timedelta(days=1)
        return moved_at

    def _infer_owners_for_unassigned_cards(self, db: Session, board_id: int) -> int:
        """Déduit un propriétaire pour les cartes SANS membre Trello (idMembers vide) mais
        clairement travaillées par un agent — cas confirmé métier : une carte est déplacée en
        "En cours" par un dev, un PR est créé, la carte avance jusqu'à Terminé, sans que
        personne ne se soit jamais ajouté comme membre Trello dessus. Une telle carte ne doit
        pas rester "non assigné" côté reporting.

        Source du signal : l'ACTEUR (memberCreator Trello) du dernier passage de la carte vers
        le stage "En cours" (IN_PROGRESS), retrouvé dans card_history — PAS le texte des
        commentaires "PR #xxx created by ..." : ce commentaire est souvent posté par un webhook
        Bitbucket sous le compte d'une autre personne (ex: le chef de projet), donc son
        memberCreator ne reflète pas forcément le vrai auteur du PR, alors que l'acteur du
        déplacement de liste, lui, est toujours la bonne personne.

        Ne touche JAMAIS aux cartes ayant déjà au moins un membre Trello réel : l'assignation
        explicite reste toujours prioritaire et n'est pas remplacée.

        Recalculée entièrement à CHAQUE sync (purge puis ré-inférence) : comme _sync_card_members
        (étape 5) retire déjà tout membre absent des idMembers Trello actuels, un membre déduit
        lors d'un sync précédent est automatiquement supprimé au sync suivant si la carte est
        toujours sans membre Trello — cette méthode le ré-ajoute donc à chaque fois plutôt que de
        se reposer sur un état persistant.
        """
        count = 0

        unassigned_card_ids = {
            card_id for (card_id,) in db.query(Card.id).filter(
                Card.board_id == board_id,
                ~Card.id.in_(db.query(CardMember.card_id))
            ).all()
        }

        if not unassigned_card_ids:
            return count

        for card_id in unassigned_card_ids:
            # Tous les passages vers "En cours" de la carte, du plus récent au plus ancien —
            # on ignore le chef de projet (PO_EXCLUDED_TRELLO_IDS) car un déplacement de sa
            # part ne signifie pas qu'il a travaillé la carte, et on retombe alors sur le
            # passage "En cours" précédent s'il y en a un.
            entries_in_progress = (
                db.query(CardHistory)
                .join(List, CardHistory.to_list_id == List.id)
                .filter(
                    CardHistory.card_id == card_id,
                    List.workflow_stage == WorkflowStageEnum.IN_PROGRESS,
                )
                .order_by(CardHistory.moved_at.desc())
                .all()
            )

            actor_trello_id = next(
                (
                    entry.actor_trello_id for entry in entries_in_progress
                    if entry.actor_trello_id
                    and entry.actor_trello_id not in PO_EXCLUDED_TRELLO_IDS
                ),
                None,
            )

            if not actor_trello_id:
                continue

            member = db.query(Member).filter(
                Member.trello_id == actor_trello_id
            ).first()

            if not member:
                continue

            db.add(CardMember(card_id=card_id, member_id=member.id))
            count += 1
            logger.info(
                f"  ↪️  Card #{card_id} sans membre Trello → assignée à "
                f"{member.full_name} (déduit du passage en 'En cours')"
            )

        db.commit()
        return count

    def _sync_card_dates(self, db, board_id: int) -> int:
        """
        Calcule started_at, completed_at, duration_working_days ET duration_real_seconds
        pour chaque carte, à partir de card_history — en ne retenant QUE les actions
        faites par un propriétaire actuel de la carte (card_members), en EXCLUANT
        systématiquement la PO (PO_EXCLUDED_TRELLO_IDS), et en ignorant toute action
        dont l'auteur n'est pas identifié (actor_trello_id NULL → prudence, on ignore).
        """
        START_STAGE = WorkflowStageEnum.IN_PROGRESS
        DONE_STAGES = {
            WorkflowStageEnum.WAITING_QA,
            # "Done / À valider" (WAITING_VALIDATION) manquait ici : une carte qui atteint ce
            # stage et n'en ressort jamais (ou seulement via des actions d'un ex-membre, cf.
            # filtre relevant_history) n'était donc jamais considérée comme terminée, même si
            # c'est bien son état final actuel sur Trello — confirmé métier.
            WorkflowStageEnum.WAITING_VALIDATION,
            WorkflowStageEnum.DONE_SPRINT,
            WorkflowStageEnum.DONE_PREPROD,
            WorkflowStageEnum.IN_PROD,
        }
        # Une carte renvoyée en Product/Sprint Backlog depuis "En cours" est déprogrammée : elle
        # n'est plus du tout en cours de traitement — confirmé métier. Sans traitement dédié, elle
        # gardait son started_at d'origine avec completed_at à None et apparaissait donc dans le
        # rapport comme une carte "jamais terminée" alors qu'elle n'est en réalité pas (ou plus)
        # du tout travaillée. Cf. plus bas : on réinitialise entièrement started_at/completed_at
        # quand ce cas survient.
        BACKLOG_STAGES = {
            WorkflowStageEnum.PRODUCT_BACKLOG,
            WorkflowStageEnum.SPRINT_BACKLOG,
        }

        cards = db.query(Card).filter(Card.board_id == board_id).all()

        count = 0
        for card in cards:
            # IDs Trello des propriétaires ACTUELS de la carte
            owner_trello_ids = {
                                   m.trello_id for m in card.members
                               } - PO_EXCLUDED_TRELLO_IDS

            history = (
                db.query(CardHistory)
                .join(List, CardHistory.to_list_id == List.id)
                .filter(CardHistory.card_id == card.id)
                .order_by(CardHistory.moved_at.asc())
                .all()
            )

            # Filtre : action faite par un propriétaire actuel, jamais par la PO
            relevant_history = [
                record for record in history
                if record.actor_trello_id
                   and record.actor_trello_id not in PO_EXCLUDED_TRELLO_IDS
                   and record.actor_trello_id in owner_trello_ids
            ]

            started_at = None
            completed_at = None
            # Date BRUTE (avant décalage "avant 9h = veille") de l'événement qui a produit
            # completed_at. Sert UNIQUEMENT à valider la cohérence chronologique
            # (completed_at_raw >= started_at) : le décalage de _shift_early_morning_completion
            # change la date d'ATTRIBUTION métier d'une completion, pas l'instant réel où
            # l'événement a eu lieu. Sans cette distinction, une carte démarrée l'après-midi
            # (ex: 18/06 12:18) puis terminée tôt le lendemain matin (ex: 19/06 8:31, décalée à
            # 18/06 8:31 par la règle des 9h) se retrouvait avec un completed_at "avant" son
            # started_at une fois décalé, alors que dans la réalité la completion (19/06 8:31)
            # est bien postérieure au démarrage (18/06 12:18) — ce qui invalidait à tort la date
            # de fin (mise à None) et faisait disparaître la carte des complétées.
            completed_at_raw = None

            # Vrai depuis le dernier started_at si un DONE_STAGES a été atteint sans qu'on soit
            # repassé par "En cours" depuis — sert à distinguer, lors d'un retour en "En cours",
            # un simple aller-retour Stand By/Rétro (pas de "terminé" entre les deux, cf. règle 1
            # cas principal) d'un vrai rework après complétion (cf. règle 1 cas "sinon").
            done_since_last_start = False
            # Vrai dès qu'un premier rework après complétion a eu lieu (carte "terminée" puis
            # repassée en "En cours") — confirmé métier (règle 1, cas "sinon") : une fois ce cas
            # survenu, started_at ne doit plus JAMAIS être réécrit par un futur passage en
            # "En cours" (ni par un nouveau rework, ni par un simple aller-retour Stand By/Rétro
            # qui suivrait) — c'est la toute première date de démarrage réel qui compte.
            reworked_after_done = False

            for record in relevant_history:
                stage = record.to_list.workflow_stage
                # Normalisation obligatoire ici : un CardHistory tout juste inséré PENDANT ce
                # même sync (par _sync_card_history, encore dans la session SQLAlchemy) garde sa
                # valeur Python aware d'origine, alors qu'un CardHistory déjà existant en base
                # revient NAIVE une fois relu par la requête ci-dessus (cf. _to_aware_utc) — les
                # deux peuvent se retrouver mélangés dans le même relevant_history, d'où le crash
                # "can't compare offset-naive and offset-aware datetimes" sans cette normalisation.
                moved_at = self._to_aware_utc(record.moved_at)

                if stage == START_STAGE:
                    if started_at is None:
                        # Tout premier passage en "En cours" de la carte.
                        started_at = moved_at
                    elif done_since_last_start:
                        # La carte avait atteint un stage "terminé" (DONE_STAGES) puis revient en
                        # "En cours" : c'est un rework, pas un simple aller-retour Stand By/Rétro
                        # — confirmé métier (règle 1, cas "sinon"). On NE met PAS started_at à
                        # jour : on garde la date du passage en "En cours" qui avait mené à ce
                        # "terminé" (déjà stockée dans started_at). La complétion précédente est
                        # invalidée : le travail a réellement repris, la carte n'est donc plus
                        # "terminée" tant qu'elle n'aura pas de nouveau atteint un DONE_STAGES.
                        completed_at = None
                        completed_at_raw = None
                        reworked_after_done = True
                    elif not reworked_after_done:
                        # Simple aller-retour via Stand By / Rétrospective, SANS passage par un
                        # stage "terminé" entre les deux — confirmé métier (règle 1, cas
                        # principal) : on prend la date de ce (dernier en date) passage en
                        # "En cours", pas celle du premier, car le vrai travail effectif n'a
                        # repris qu'à ce moment-là (ex: carte passée en cours le 28/05, renvoyée
                        # en Stand By, puis repassée en cours le 03/06 → on démarre le calcul à
                        # partir du 03/06, pas du 28/05).
                        started_at = moved_at
                    # Si reworked_after_done est déjà vrai, on ne touche plus jamais à
                    # started_at, quel que soit le type de retour en "En cours" (cf. plus haut).
                    done_since_last_start = False

                if stage in DONE_STAGES:
                    # On prend le DERNIER stage "terminé" (DONE_STAGES) atteint depuis le dernier
                    # retour en "En cours", pas le premier — confirmé métier (règle 2) : un simple
                    # passage en "A vérifier / QA" ne garantit pas que la carte est réellement
                    # terminée, elle peut encore revenir en "En cours" pour du rework. On ne
                    # fige donc la date de fin qu'au DERNIER stage "terminé" atteint avant un
                    # éventuel retour en "En cours" (ex: QA le 5 puis Préprod le 8 → la carte est
                    # considérée terminée le 8, pas le 5 ; si la carte reste en QA sans jamais
                    # avancer ni revenir en "En cours", elle reste considérée terminée le 5).
                    completed_at_raw = moved_at
                    completed_at = self._shift_early_morning_completion(moved_at)
                    done_since_last_start = True

                if stage in BACKLOG_STAGES:
                    # La carte est déprogrammée (renvoyée en Product/Sprint Backlog) : on
                    # réinitialise tout comme si elle n'avait jamais démarré — confirmé métier.
                    # Contrairement au retour en Stand By/Rétro (règle 1), qui garde une date de
                    # démarrage car le travail reprendra "bientôt", un retour en backlog signifie
                    # que la carte n'est plus du tout d'actualité pour l'instant : ni started_at
                    # ni completed_at ne doivent rester renseignés. Si elle repasse un jour en
                    # "En cours", elle repartira sur un cycle tout neuf (started_at = cette
                    # nouvelle date, via la branche "started_at is None" ci-dessus) — y compris si
                    # un rework après complétion avait eu lieu avant : le passage en backlog
                    # efface aussi ce gel (reworked_after_done).
                    started_at = None
                    completed_at = None
                    completed_at_raw = None
                    done_since_last_start = False
                    reworked_after_done = False

            # Deux signaux PEUVENT indiquer la fin d'une carte : atteindre un stage "terminé"
            # (à valider / done / fini ce sprint / terminé (preprod) / en prod / à vérifier-QA
            # — cf. DONE_STAGES) OU avoir un PR créé. Le PR est PRIORITAIRE sur le stage Trello,
            # peu importe lequel est le plus récent — confirmé métier (ex: PR posté le 2, carte
            # déplacée en "Done / À valider" seulement le 10 → la carte est considérée terminée
            # le 2, pas le 10 : le code était réellement prêt dès le PR, le déplacement Trello
            # n'est qu'une formalité administrative qui traîne). Une carte peut avoir plusieurs
            # PR (corrections successives) : last_pr_comment_at retient déjà le PLUS RÉCENT
            # (cf. _sync_card_history). Le stage Trello ne sert de date de fin QUE s'il n'y a
            # aucun PR du tout sur la carte.
            if card.last_pr_comment_at:
                completed_at = self._to_aware_utc(card.last_pr_comment_at)
                # Pas de décalage 9h sur un commentaire de PR (cf. docstring de
                # _shift_early_morning_completion) : la date brute est la date elle-même.
                completed_at_raw = completed_at

            # Carte qui saute directement du backlog (Product Backlog / Sprint Backlog) à un
            # stage terminé, SANS jamais passer par IN_PROGRESS (donc started_at encore None à
            # ce stade) : on la considère démarrée ET terminée le même jour que sa date de fin —
            # confirmé métier. Sans ce cas, started_at restait None pour toujours et la carte
            # disparaissait purement et simplement du rapport (aucune activité mesurable),
            # alors qu'elle a bien été traitée, juste très vite / sans étape "En cours" tracée.
            if started_at is None and completed_at is not None:
                started_at = completed_at

            # Validation de cohérence sur la date BRUTE (avant décalage 9h) — cf. commentaire sur
            # completed_at_raw plus haut. On ne compare plus started_at au completed_at déjà
            # décalé, pour ne pas invalider à tort des cartes terminées tôt le matin juste après
            # un démarrage l'après-midi de la veille. Un completed_at_raw < started_at signifie
            # que l'événement de fin lui-même (avant tout décalage métier) précède le démarrage :
            # c'est une VRAIE incohérence de données (pas un simple effet du décalage 9h) → on
            # invalide.
            if started_at and completed_at_raw and completed_at_raw < started_at:
                logger.warning("Ignoring invalid completion date for card %s", card.id)
                completed_at = None
                completed_at_raw = None
            elif started_at and completed_at and completed_at < started_at:
                # Le décalage "avant 9h = veille" a ramené completed_at à une date/heure
                # antérieure à started_at (ex: carte démarrée à 12:18 puis passée en stage
                # terminé le lendemain à 8:31, décalée à la veille 8:31 < 12:18 le même jour).
                # L'événement réel (completed_at_raw) est bien postérieur à started_at — validé
                # ci-dessus — donc ce n'est PAS une incohérence de données, juste un effet de
                # bord du décalage métier. On clamp à started_at plutôt que d'invalider : la
                # DATE reste la même (jour voulu par la règle des 9h), seule l'heure de stockage
                # est ajustée, et la contrainte SQL chk_dates_order (completed_at >= started_at)
                # est respectée.
                completed_at = started_at

            # card.started_at / card.completed_at reviennent NAIVE (relus depuis la DB juste
            # au-dessus, cf. requête `cards = db.query(Card)...`) tandis que started_at /
            # completed_at sont désormais toujours AWARE (normalisés plus haut) — d'où
            # _to_aware_utc pour ne pas lever TypeError. On floor aussi à la seconde (cf.
            # _floor_to_second) : la colonne DATETIME peut avoir tronqué la sous-seconde au
            # stockage précédent, sans quoi CHAQUE carte serait réécrite à chaque sync même sans
            # changement réel.
            changed = False
            if self._floor_to_second(self._to_aware_utc(card.started_at)) != self._floor_to_second(started_at):
                card.started_at = started_at
                changed = True
            if self._floor_to_second(self._to_aware_utc(card.completed_at)) != self._floor_to_second(completed_at):
                card.completed_at = completed_at
                changed = True

            # Durée "jours ouvrés" — conservée pour le Gantt existant
            duration_days = self._working_days_between(started_at, completed_at)
            if card.duration_working_days != duration_days:
                card.duration_working_days = duration_days
                changed = True

            # NOUVEAU — durée réelle précise (secondes), pour affichage Xh Ymin.
            # On utilise completed_at_raw (l'instant RÉEL de l'événement, avant décalage 9h et
            # avant clamp) plutôt que completed_at : sinon la durée serait faussement nulle/
            # négative pour une carte démarrée l'après-midi et terminée tôt le lendemain matin,
            # alors qu'en vrai du temps de travail s'est écoulé entre les deux (cf. clamp
            # ci-dessus). completed_at_raw a déjà été validé >= started_at plus haut.
            duration_seconds = None
            if started_at and completed_at_raw:
                duration_seconds = int((completed_at_raw - started_at).total_seconds())
            if card.duration_real_seconds != duration_seconds:
                card.duration_real_seconds = duration_seconds
                changed = True

            if changed:
                count += 1

        db.commit()
        return count

    def _working_days_between(self, start, end) -> "int | None":
        """Nombre de jours ouvrés (lun-ven) entre deux datetimes, bornes incluses. None si l'une manque."""
        if not start or not end:
            return None
        if end < start:
            logger.warning(f"⚠️  completed_at ({end}) antérieur à started_at ({start}) — duration mise à NULL")
            return None

        current = start.date()
        end_date = end.date()
        working_days = 0
        while current <= end_date:
            if current.weekday() < 5:  # 0=lundi ... 4=vendredi
                working_days += 1
            current += timedelta(days=1)
        return working_days if working_days > 0 else None