import logging
import os
import random
import time
import requests
from typing import TypeVar, Dict, Any, List, Optional
from dotenv import load_dotenv

T = TypeVar("T")

load_dotenv()

logger = logging.getLogger(__name__)

# Nombre de tentatives supplémentaires sur un 429 (Too Many Requests) avant d'abandonner. Trello
# ne renvoie pas toujours un header Retry-After exploitable, d'où le backoff exponentiel en
# secours (RETRY_BACKOFF_BASE_SECONDS * 2**tentative) si l'en-tête est absent.
MAX_RATE_LIMIT_RETRIES = 5
RETRY_BACKOFF_BASE_SECONDS = 1.0

class TrelloService:
    BASE_URL = "https://api.trello.com/1"

    def __init__(self):
        self.api_key = os.getenv("TRELLO_API_KEY")
        self.api_token = os.getenv("TRELLO_API_TOKEN")

        if not self.api_key or not self.api_token:
            raise ValueError(
                "❌ TRELLO_API_KEY ou TRELLO_API_TOKEN manquent dans .env\n"
            )

        self.auth_params = {
            "key": self.api_key,
            "token": self.api_token
        }

        logger.info("✅ Trello Service initialized")
        logger.info(f"   Key: {self.api_key[:8]}...")
        logger.info(f"   Token: {self.api_token[:8]}...")

    def _request(self, method: str, endpoint: str, **kwargs) -> T:
        url = f"{self.BASE_URL}/{endpoint}"

        params = kwargs.pop("params", {})
        params.update(self.auth_params)

        attempt = 0
        while True:
            try:
                print(f"[API] {method} {endpoint}")
                response = requests.request(
                    method=method,
                    url=url,
                    params=params,
                    timeout=10,
                    **kwargs
                )

                if response.status_code == 429:
                    if attempt >= MAX_RATE_LIMIT_RETRIES:
                        response.raise_for_status()  # laisse lever la HTTPError normale

                    # Trello inclut parfois un Retry-After (secondes) ; sinon on retombe sur un
                    # backoff exponentiel local. Sans ce retry, un simple pic de concurrence
                    # (ex: plusieurs cartes fetchées en parallèle, cf. sync_service) fait échouer
                    # tout le sync au lieu de simplement ralentir un peu.
                    retry_after = response.headers.get("Retry-After")
                    wait_seconds = (
                        float(retry_after) if retry_after
                        # + jitter aléatoire : sans ça, des workers parallèles qui reçoivent
                        # tous un 429 en même temps ressortiraient tous de leur time.sleep()
                        # exactement en même temps et se reprendraient un 429 groupé.
                        else RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)
                    )
                    logger.warning(
                        f"⏳ 429 Too Many Requests sur {endpoint} — "
                        f"nouvelle tentative dans {wait_seconds:.1f}s "
                        f"(essai {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})"
                    )
                    time.sleep(wait_seconds)
                    attempt += 1
                    continue

                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                print(f"❌ Erreur API: {e}")
                raise

    # ========================================================================
    # 1. BOARDS
    # ========================================================================

    def get_boards(self) -> List[Dict[str, Any]]:
        return self._request("GET", "members/me/boards")

    def get_board_by_id(self, board_id: str) -> Dict[str, Any]:
        return self._request("GET", f"boards/{board_id}",params={
            "fields": "name,url,desc,dateLastActivity"
        })

    # ========================================================================
    # 2. LISTES (Lists)
    # ========================================================================

    def get_board_lists(self, board_id: str) -> List[Dict[str, Any]]:
        # filter=all (et pas le défaut "open") : une liste archivée sur Trello (ex: réorganisation
        # du board) doit quand même être synchronisée avec son workflow_stage correct, puisque
        # des cartes y sont passées historiquement et que card_history y référence encore cette
        # liste via to_list_id. Sans ça, ces mouvements historiques ne peuvent jamais être
        # rattachés à un stage, et une carte peut sembler "jamais terminée" alors qu'elle l'est.
        return self._request(
            "GET",
            f"boards/{board_id}/lists",
            params={"filter": "all"},
        )

    def get_list_by_id(self, list_id: str) -> Dict[str, Any]:
        return self._request("GET", f"lists/{list_id}")

    # ========================================================================
    # 3. CARTES (Cards)
    # ========================================================================

    def get_board_cards(self, board_id: str) -> List[Dict[str, Any]]:
        return self._request(
            "GET",
            f"boards/{board_id}/cards",
            params={
                "fields": "all",
                "members": "true",
                "labels": "true",
                "customFieldItems": "true",
                "attachments": "true"
            }
        )

    def get_list_cards(self, list_id: str) -> List[Dict[str, Any]]:
        return self._request(
            "GET",
            f"lists/{list_id}/cards",
            params={
                "fields": "all",
                "members": "true",
                "labels": "true",
                "attachments": "true"
            }
        )

    def get_card_by_id(self, card_id: str):
        return self._request(
            "GET",
            f"cards/{card_id}",
            params={
                "fields": "all",
                "members": "true",
                "member_fields": "fullName,username,avatarHash",
                "labels": "true",
                "checklists": "all",
                "attachments": "true",
                "attachment_fields": "all"
            }
        )

    # ========================================================================
    # 4. MEMBRES (Members)
    # ========================================================================

    def get_board_members(self, board_id: str) -> List[Dict[str, Any]]:
        params = {
            "fields": "fullName,username,avatarHash,email"
        }
        return self._request("GET", f"boards/{board_id}/members", params=params)

    def get_current_user(self) -> Dict[str, Any]:
        return self._request("GET", "members/me")

    # ========================================================================
    # 5. LABELS
    # ========================================================================

    def get_board_labels(self, board_id: str) -> List[Dict[str, Any]]:
        return self._request(
            "GET",
            f"boards/{board_id}/labels",
            params={"fields": "all", "limit": "1000"},
        )

    # ========================================================================
    # 6. ACTIONS (historique)
    # ========================================================================

    def get_card_actions(self, card_id: str, action_types: Optional[str] = None, limit: int = 1000) -> List[
        Dict[str, Any]]:
        params = {
            "fields": "all",
            "limit": str(limit),
            "memberCreator": "true",
            "memberCreator_fields": "fullName,username",
        }
        if action_types:
            params["filter"] = action_types

        return self._request("GET", f"cards/{card_id}/actions", params=params)