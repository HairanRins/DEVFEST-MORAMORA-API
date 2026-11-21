# MoraMora API — Fonctionnement, installation et structure

## 1. Vue d'ensemble

Le dépôt contient deux parties :

| Dossier         | Rôle                                                                                                                                                   |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `moramora_api/` | Projet **Django** : API REST d'authentification (JWT) + **WebSocket** de détection d'infractions en temps réel.                                        |
| `violation/`    | Moteur d'**IA** : modèle **YOLOv8** (Ultralytics) téléchargé depuis Hugging Face, utilisé par le WebSocket. Contient aussi un script de démo autonome. |

Le serveur tourne en **ASGI** avec **Daphne** (via Django Channels), ce qui permet de servir à la fois HTTP et WebSocket sur le même port (`8000`). Le _channel layer_ de Channels est **en mémoire** (aucun Redis requis), et la base utilisée est **Neon PostgreSQL** via `DATABASE_URL`.

```
                 ┌───────────────────────── Daphne (ASGI, port 8000) ─────────────────────────┐
 Frontend        │                                                                             │
 (React/Vite…)   │  HTTP  /api/auth/*  ──► Django REST Framework ──► authentication (views)    │
   │             │                                       │                                     │
   │  POST login │                                       ▼                                     │
    ├────────────►│                          Base de données (Neon / SQLite local)              │

   │  ◄── JWT    │                          User + IssuedAccessToken (registre des JWT)        │
   │             │                                       ▲                                     │
   │  WS         │  WebSocket /ws/detection/             │ vérifie le JWT                      │
   └────────────►│   OriginValidator ─► JWTAuthMiddleware ─► DetectionConsumer                 │
     image JPEG  │                                                   │                         │
     /PNG        │                                     image_adapter (décodage, limites)       │
   ◄── résultat  │                                                   ▼                         │
     + alerte    │                          violation/inference.py ─► YOLOv8 (best.pt HF)      │
                 └─────────────────────────────────────────────────────────────────────────────┘
                                          Channel layer Channels (en mémoire)

```

---

## 2. Comment ça fonctionne

### 2.1 Démarrage (`moramora_api/moramora_api/`)

- `settings.py` charge `.env` (python-dotenv), ajoute la **racine du dépôt** au `sys.path` (pour pouvoir importer le paquet `violation`), configure la base (Neon via `DATABASE_URL`, en direct sauf si `DJANGO_DB_USE_POOLED=true`, sinon SQLite en repli), DRF, SimpleJWT, CORS, Channels avec channel layer en mémoire et les limites d'image WebSocket.
- `asgi.py` construit l'application ASGI :
  - `http` → application Django classique ;
  - `websocket` → `RequireWebSocketOrigin` (refuse sans en-tête `Origin`, code 4403) → `OriginValidator` (origines de `WEBSOCKET_ALLOWED_ORIGINS`) → `URLRouter` (routes de `detection_ws/routing.py`).
- `urls.py` monte `api/auth/` sur l'app `authentication`.

### 2.2 Authentification REST (`authentication/`)

| Endpoint             | Méthode | Auth | Effet                                                                                                                                                                        |
| -------------------- | ------- | ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/auth/signup/`  | POST    | non  | Crée un utilisateur (`username`, `email`, `password`, `password_confirmation`). Email/username uniques (insensibles à la casse), validateurs de mot de passe Django. → `201` |
| `/api/auth/login/`   | POST    | non  | `email` + `password` → renvoie `data.access` (JWT). Limité à `LOGIN_RATE_LIMIT` (5/min par défaut).                                                                          |
| `/api/auth/profile/` | GET     | oui  | Renvoie id, username, email, `date_joined`, `last_login`.                                                                                                                    |
| `/api/auth/logout/`  | POST    | oui  | Révoque le JWT courant.                                                                                                                                                      |

Principe des tokens :

1. `tokens.py` → `UnlimitedAccessToken` : JWT HS256 **sans expiration** (`exp` non posé).
2. Au login, le `jti` (identifiant unique du token) est enregistré dans la table `IssuedAccessToken`.
3. `authentication.py` → `RegisteredTokenAuthentication` : vérifie la signature (SimpleJWT) **puis** que le `jti` existe en base et n'est pas révoqué.
4. Logout = `revoked_at` renseigné → token immédiatement invalide.
5. `services.revoke_all_tokens_for_user(user)` révoque toutes les sessions d'un compte (pas d'endpoint, usage dans le code).

Le client envoie le token dans `Authorization: Bearer <JWT>`. Les erreurs sont normalisées par `exceptions.py` : `{"success": false, "message": "...", "errors": ...}`.

### 2.3 WebSocket de détection (`detection_ws/`)

URL : `ws://localhost:8000/ws/detection/`

1. **Authentification** (`authentication.py` → `JWTAuthMiddleware`) : le JWT est lu soit dans l'en-tête `Authorization: Bearer`, soit (navigateur) via les sous-protocoles `["bearer", <token>]`. Mêmes vérifications que l'API REST (signature, utilisateur actif, `jti` non révoqué). Un JWT dans l'URL est refusé.
2. **Connexion** (`consumers.py` → `DetectionConsumer.connect`) : utilisateur non authentifié → fermeture code `4401`.
3. **Réception d'une image** : trame binaire JPEG/PNG, ou JSON `{"type":"image","image":"<base64 ou data URL>"}`.
4. **Validation** (`image_adapter.py`) : type JPEG/PNG par signature, taille ≤ `WEBSOCKET_MAX_IMAGE_SIZE` (5 Mio), pixels ≤ `WEBSOCKET_MAX_IMAGE_PIXELS` (20 MP, vérifié dans l'en-tête _avant_ décodage), puis décodage OpenCV.
5. **Inférence** (`services/violation_service.py` → `violation/inference.py`) : exécutée dans un thread (`asyncio.to_thread`). Le modèle est chargé une seule fois (`lru_cache`). Un verrou n'autorise qu'**une inférence à la fois** : si occupé, erreur `busy` (le client doit réessayer).
6. **Réponses envoyées** :
   - `{"type":"detection_result","success":true,"data":{"image":"<JPEG annoté base64>","detections":[{class_id,label,confidence,bbox:{x1,y1,x2,y2}}],...}}`
   - si au moins une détection : `{"type":"alert","event":"DETECTION_ALERT",...}`
   - en cas d'erreur : `{"type":"error","success":false,"code":"invalid_image|busy|processing_error","message":"..."}`

Exemple côté navigateur :

```javascript
const socket = new WebSocket("ws://localhost:8000/ws/detection/", [
  "bearer",
  accessToken,
]);
socket.onopen = () =>
  socket.send(JSON.stringify({ type: "image", image: base64JpegOrPng }));
socket.onmessage = ({ data }) => console.log(JSON.parse(data));
```

### 2.4 Moteur IA (`violation/`)

- `inference.py` : télécharge `best.pt` depuis le dépôt Hugging Face `jashwanthpeddisetty0712/violation-detection-yolov8` (mis en cache dans `~/.cache/huggingface`), charge YOLO, prédit avec `conf=0.5`, renvoie l'image annotée + la liste des détections.
- `main.py` : démo autonome (image + vidéo `test.mp4`), résultats écrits dans `violation/runs/detect/predict/`. N'exécute rien quand il est seulement importé.

---

## 3. Ce qu'il faut installer

### 3.1 Sur la machine

| Outil                                                     | Pourquoi                                                                | Remarque                                                                                                    |
| --------------------------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **Python 3.10+** (3.12 conseillé) + `python3-venv`        | Exécuter Django et YOLO                                                 | `sudo apt install python3 python3-venv python3-pip`                                                         |
| **Compte Neon**                                           | Base PostgreSQL hébergée, accessible via `DATABASE_URL`                 | Garder `sslmode=require&channel_binding=require` dans l'URL                |
| **Docker** (optionnel)                                    | Utile pour les démos, plus nécessaire pour la base ou le WebSocket     |                                                                                                             |
| **SQLite** (repli uniquement)                             | Sans `DATABASE_URL`, pour un test local isolé                          | Inclus avec Python                                                                                          |
| **Bibliothèques système OpenCV** (si erreur `libGL.so.1`) | Requises par `opencv-python` sur certains Linux                         | `sudo apt install libgl1 libglib2.0-0`                                                                      |
| **Connexion Internet** (1ʳᵉ fois)                         | Télécharger les poids YOLO depuis Hugging Face et PyTorch via pip       | Plusieurs centaines de Mo pour PyTorch                                                                      |
| **GPU NVIDIA + CUDA** (optionnel)                         | Accélérer l'inférence                                                   | Fonctionne aussi sur CPU                                                                                    |

### 3.2 Dans le projet (`moramora_api/requirements.txt`, environnement virtuel)

| Paquet                          | Rôle                                                           |
| ------------------------------- | -------------------------------------------------------------- |
| `Django` (5.2 – 6.x)            | Framework web, ORM, migrations                                 |
| `djangorestframework`           | API REST (vues, sérialiseurs, throttling)                      |
| `djangorestframework-simplejwt` | Génération/validation des JWT                                  |
| `django-cors-headers`           | CORS pour le frontend (ports 3000 / 5173)                      |
| `python-dotenv`                 | Chargement du fichier `.env`                                   |
| `psycopg[binary]`               | Pilote PostgreSQL                                              |
| `channels[daphne]`              | WebSocket + serveur ASGI Daphne (`runserver` passe par Daphne) |
| `channels.layers` (inclus)      | Channel layer en mémoire, sans Redis                           |
| `ultralytics`                   | YOLOv8 (installe aussi **PyTorch**, torchvision, numpy…)       |
| `opencv-python`                 | Décodage / encodage d'images                                   |

> ⚠️ `violation/inference.py` importe `huggingface_hub`, qui n'est pas listé explicitement dans `requirements.txt`. S'il n'est pas installé par une dépendance transitive, faire : `pip install huggingface_hub`.

### 3.3 Procédure d'installation (Linux)

```bash
cd moramora_api
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install huggingface_hub   # si absent

cp .env.example .env                     # puis renseigner DATABASE_URL (Neon)
python manage.py migrate                 # sur l'URL directe, une seule fois

# Serveur (HTTP + WebSocket via Daphne, channel layer en mémoire)
python manage.py runserver
```

Tests :

```bash
python manage.py test authentication detection_ws
# Test d'intégration avec le vrai modèle YOLO :
RUN_MODEL_INTEGRATION_TESTS=1 python manage.py test \
  detection_ws.tests.DetectionWebSocketTests.test_real_model_through_websocket
```

Démo IA seule (depuis la racine du dépôt) : `python violation/main.py`

### 3.4 Variables d'environnement (`.env`)

| Variable                                                                     | Défaut                     | Description                                                    |
| ---------------------------------------------------------------------------- | -------------------------- | -------------------------------------------------------------- |
| `DJANGO_SECRET_KEY`                                                          | clé de dev                 | **Obligatoire** si `DEBUG=false` ; sert aussi à signer les JWT |
| `DJANGO_DEBUG`                                                               | `true`                     | Mode debug                                                     |
| `DJANGO_ALLOWED_HOSTS`                                                       | `localhost,127.0.0.1`      | Hôtes autorisés                                                |
| `DATABASE_URL`                                                               | —                          | URL Neon **directe** (utilisée aussi pour `migrate`)           |
| `DATABASE_URL_POOLED`                                                        | —                          | URL Neon poolée, optionnelle                                   |
| `DJANGO_DB_USE_POOLED`                                                       | `false`                    | `true` pour utiliser l'URL poolée (jamais pour les migrations) |
| `DB_CONN_MAX_AGE`                                                            | `0`                        | Durée de vie des connexions avec `DATABASE_URL`                |
| `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`       | —                          | Ancien mode PostgreSQL local, ignoré si `DATABASE_URL` est set |
| `CORS_ALLOWED_ORIGINS`                                                       | localhost:3000/5173        | Origines HTTP autorisées                                       |
| `LOGIN_RATE_LIMIT`                                                           | `5/min`                    | Limite de tentatives de login                                  |
| `WEBSOCKET_ALLOWED_ORIGINS`                                                  | localhost:3000/5173        | Origines WebSocket autorisées                                  |
| `WEBSOCKET_MAX_IMAGE_SIZE`                                                   | `5242880` (5 Mio)          | Taille max d'une image                                         |
| `WEBSOCKET_MAX_IMAGE_PIXELS`                                                 | `20000000`                 | Nombre max de pixels                                           |

### 3.5 Migration Neon

La procédure complète (snapshot, `migrate --plan`, migration unique sur l'URL
directe, import optionnel depuis SQLite) est décrite dans
`moramora_api/README.md`, section « Migration vers Neon ». Les variables
`NEON_AUTH_*`, `AWS_*`, `S3_BUCKET` et `NEON_REALTIME_*` présentes dans `.env`
ne sont pas encore consommées par le code Django.

---

## 4. Structure du dépôt

```
devfest-moramora-api/
├── STRUCTURE.md                      # Ce document
├── moramora_api/                     # Projet Django
│   ├── manage.py                     # CLI Django (migrate, runserver, test…)
│   ├── requirements.txt              # Dépendances Python
│   ├── .env.example                  # Modèle de configuration à copier en .env
│   ├── README.md                     # Doc API (exemples curl, WebSocket)
│   ├── moramora_api/                 # Configuration du projet
│   │   ├── settings.py               # Réglages (DB, JWT, CORS, Channels, limites WS)
│   │   ├── urls.py                   # Routes HTTP → /api/auth/
│   │   ├── asgi.py                   # Point d'entrée ASGI (HTTP + WebSocket, contrôle d'origine)
│   │   └── wsgi.py                   # Point d'entrée WSGI (HTTP seul, non utilisé pour le WS)
│   ├── authentication/               # App : comptes et JWT
│   │   ├── models.py                 # User (email unique) + IssuedAccessToken (registre/révocation)
│   │   ├── tokens.py                 # UnlimitedAccessToken (JWT sans expiration)
│   │   ├── authentication.py         # RegisteredTokenAuthentication (JWT + vérif en base)
│   │   ├── serializers.py            # Validation signup/login, sortie profil
│   │   ├── views.py                  # Signup, Login, Logout, Profile
│   │   ├── urls.py                   # signup/ login/ logout/ profile/
│   │   ├── services.py               # revoke_all_tokens_for_user()
│   │   ├── exceptions.py             # Format d'erreur uniforme
│   │   ├── apps.py
│   │   ├── migrations/               # 0001_initial, 0002 (username unique insensible à la casse)
│   │   └── tests.py
│   └── detection_ws/                 # App : WebSocket de détection
│       ├── routing.py                # Route ws/detection/ (+ JWTAuthMiddleware)
│       ├── authentication.py         # JWTAuthMiddleware (header ou sous-protocole)
│       ├── consumers.py              # DetectionConsumer : reçoit image → résultat/alerte/erreur
│       ├── image_adapter.py          # Décodage base64/binaire, contrôles taille/pixels, encodage JPEG
│       ├── services/
│       │   └── violation_service.py  # Pont vers violation.inference
│       ├── apps.py
│       └── tests.py
└── violation/                        # Moteur IA (paquet Python importé par Django)
    ├── __init__.py
    ├── inference.py                  # load_model() (HF + YOLO, en cache), detect_image() (verrou)
    ├── main.py                       # Démo autonome image + vidéo
    ├── Image0775_jpg…jpg             # Image d'exemple
    ├── test.mp4                      # Vidéo d'exemple
    └── runs/detect/predict/          # Sorties générées par la démo
```

Fichiers générés localement (non versionnés) : `moramora_api/.env`, `moramora_api/.venv/`, `moramora_api/db.sqlite3`, cache des poids `~/.cache/huggingface/`.
