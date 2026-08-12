# TrendLabs — Reporting Tool

Outil de reporting interne qui synchronise plusieurs boards Trello vers une base de données, puis génère automatiquement des rapports Excel (mensuels et annuels) de suivi de charge de travail par développeur et par projet.

## Introduction

Avant cet outil, le suivi de la charge de travail des développeurs se faisait manuellement à partir de Trello. TrendLabs automatise ce processus de bout en bout :

* Synchronisation régulière de plusieurs boards Trello (cartes, membres, labels, historique des déplacements)
* Génération d'un **rapport mensuel** (grille Gantt Excel) pour un board + un projet + un mois donnés
* Génération d'un **rapport annuel** (vue d'ensemble multi-board / multi-projet) sur une période libre
* Détection automatique des sprints pertinents, avec possibilité de les ajuster manuellement
* Accès protégé par authentification (email + mot de passe)

## Fonctionnalités

* **Synchronisation Trello → base de données** : upserts idempotents, synchronisation incrémentale (`dateLastActivity`), isolation des erreurs par action (SAVEPOINTs), mapping flou des noms de listes vers un workflow interne
* **Rapport mensuel** : grille Gantt par sprint, couleur unique par développeur, détection automatique des sprints du mois (modifiable), export `.xlsx`
* **Rapport annuel** : période libre, tous boards/projets par défaut, filtres optionnels (boards, sprints), génération asynchrone avec suivi de statut, vue d'ensemble avec heatmap de couleur et taux d'occupation, feuille de traçabilité des paramètres du run
* **Règles métier d'attribution du travail** :
  * un passage de carte vers un stage "terminé" avant 9h (heure de Tunis) est attribué à la veille
  * un commentaire de Pull Request est prioritaire sur le déplacement Trello comme signal de fin de tâche
  * le Product Owner est exclu du calcul des jours travaillés
  * seul le développeur ayant réellement déplacé la carte vers "En cours" est crédité (pas tous les membres assignés)
* **Authentification** : JWT (email + mot de passe), comptes créés automatiquement au démarrage

## Architecture

Le projet est conteneurisé avec Docker Compose, en 3 services :

* **`mysql`** — base de données MySQL 8.0, schéma initialisé automatiquement via `schema_trendlabs.sql`
* **`backend`** — API FastAPI (Python 3.10)
* **`frontend`** — application React + Vite

### Flux de bout en bout

* La synchronisation interroge l'API Trello et met à jour la base (`sync_service.py`)
* Le frontend appelle un endpoint de génération de rapport (mensuel ou annuel) avec les paramètres choisis
* Le service correspondant (`excel_service.py` ou `annual_report_service.py`) interroge la base, applique les règles métier, et génère un fichier `.xlsx` avec `openpyxl`
* Le fichier est stocké côté serveur, son statut tracé en base, puis téléchargé depuis le frontend

## Stack technique

**Backend**

* FastAPI (Python 3.10) — API REST, validation Pydantic, documentation Swagger auto-générée
* SQLAlchemy — ORM
* MySQL 8.0 — base de données relationnelle
* openpyxl — génération des fichiers Excel
* python-jose — génération/validation des tokens JWT
* bcrypt — hash des mots de passe

**Frontend**

* React 18 + Vite
* React Router v6
* TanStack Query (react-query)
* Tailwind CSS
* lucide-react (icônes)

**Infrastructure**

* Docker / Docker Compose
* API Trello (REST) — source des données synchronisées

## Structure du projet

```
trendlabs/
├── backend/
│   ├── app/
│   │   ├── main.py                          # point d'entrée FastAPI
│   │   ├── database/
│   │   │   └── db.py                        # connexion SQLAlchemy, get_db, SessionLocal
│   │   ├── models/
│   │   │   └── models.py                    # modèles ORM (Board, Card, Label, User, ...)
│   │   ├── core/
│   │   │   └── security.py                  # dépendance get_current_user (JWT)
│   │   ├── services/
│   │   │   ├── sync/
│   │   │   │   └── sync_service.py          # synchronisation Trello → base
│   │   │   ├── excel/
│   │   │   │   ├── excel_service.py         # génération du rapport mensuel
│   │   │   │   ├── annual_report_service.py # génération du rapport annuel
│   │   │   │   └── sprint_discovery_service.py
│   │   │   └── auth/
│   │   │       ├── auth_service.py          # hash mot de passe, JWT
│   │   │       └── seed_users.py            # création des comptes par défaut
│   │   └── api/v1/endpoints/
│   │       ├── sync_endpoints.py
│   │       ├── excel_endpoints.py
│   │       ├── annual_report_endpoints.py
│   │       ├── sprint_discovery_endpoints.py
│   │       └── auth_endpoints.py
│   ├── schema_trendlabs.sql                 # schéma de base, appliqué au démarrage du conteneur MySQL
│   └── requirements.txt
└── frontend/
    └── src/
        ├── main.jsx                         # point d'entrée (QueryClient, Router, AuthProvider)
        ├── App.jsx                          # routes, garde d'authentification
        ├── api/                             # appels réseau (reports, annualReports, auth, http)
        ├── hooks/                           # hooks React Query
        └── features/
            ├── reports/components/          # formulaires mensuel/annuel, sidebar, historique
            ├── boards/components/
            └── auth/                        # login, contexte d'authentification
```

## Prérequis

* Docker et Docker Compose
* Un compte Trello avec une clé API et un token (accès aux boards à synchroniser)
* Node.js 18+ et Python 3.10+ (uniquement si vous développez en dehors de Docker)

## Cloner le projet

```bash
git clone <URL_DU_REPO>
cd trendlabs
```

## Configuration

Créez (ou complétez) le fichier `.env` du backend (`backend/.env`) :

```env
# Base de données — à adapter selon votre configuration MySQL existante
DB_HOST=mysql
DB_PORT=3306
DB_USER=root
DB_PASSWORD=changeme
DB_NAME=trendlabs

# API Trello
TRELLO_API_KEY=<votre_clé_api_trello>
TRELLO_TOKEN=<votre_token_trello>

# Authentification (JWT)
AUTH_SECRET_KEY=<généré avec: openssl rand -hex 32>
SEED_PASSWORD_YOSSR=<mot de passe du 1er compte>
SEED_PASSWORD_AHLEM=<mot de passe du 2e compte>
SEED_PASSWORD_WAJIH=<mot de passe du 3e compte>
```

Et dans le frontend (`frontend/.env`) :

```env
VITE_API_URL=http://localhost:8000
```

## Démarrer le projet

```bash
docker compose up --build
```

Au premier démarrage :

* Le schéma de base est créé automatiquement depuis `schema_trendlabs.sql`
* Les 3 comptes utilisateurs sont créés automatiquement (idempotent — rejoué sans doublon à chaque redémarrage)

L'application est ensuite accessible sur :

* Frontend : `http://localhost:5173`
* API backend : `http://localhost:8000`
* Documentation Swagger : `http://localhost:8000/docs`

### Première utilisation

* Se connecter avec l'un des 3 comptes configurés
* Synchroniser un board Trello depuis l'interface (bouton "Synchroniser un nouveau board")
* Générer un rapport mensuel ou annuel depuis la sidebar

## Documentation API

La documentation interactive complète (Swagger) est disponible sur `/docs` une fois le backend démarré. Endpoints principaux :

* `POST /api/auth/login` — connexion, retourne un token JWT
* `GET /api/sync/debug/boards` — liste des boards synchronisés
* `POST /api/sync/full/{trello_board_id}` — synchronise un board
* `POST /api/reports/generate/{trello_board_id}` — génère un rapport mensuel
* `POST /api/reports/annual/generate` — génère un rapport annuel (asynchrone)
* `GET /api/reports/annual/{id}` — statut d'un rapport annuel en cours de génération

## Logs et débogage

```bash
docker compose logs -f backend
```

Les étapes de synchronisation et de génération de rapport sont journalisées en détail (cartes exclues, règles métier appliquées, etc.).

## Règles métier — pourquoi ces choix

* **Décalage 9h** : un dev qui oublie de déplacer sa carte le soir et le fait le lendemain matin en arrivant ne doit pas se voir crédité d'une journée de travail fictive
* **Priorité PR** : le code est réellement prêt dès la création de la Pull Request ; le déplacement Trello n'est souvent qu'une formalité administrative qui traîne
* **Exclusion du Product Owner** : un déplacement de carte par le PO ne reflète pas un travail de développement
* **Attribution par acteur réel (rapport annuel)** : seul le développeur ayant lui-même déplacé la carte vers "En cours" est crédité, pas l'ensemble des membres assignés à la carte

## Auteur

Projet réalisé dans le cadre d'un stage — Faculté des Sciences de Tunis.
