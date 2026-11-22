# MoraMora — Frontend

Interface React (Vite) du backend MoraMora :

- **Inscription / connexion** (`/api/auth/signup/`, `/api/auth/login/`), JWT
  conservé dans `localStorage`, profil (`/api/auth/profile/`), déconnexion
  avec révocation (`/api/auth/logout/`).
- **Détection** via le WebSocket `/ws/detection/` (sous-protocole
  `["bearer", token]`) :
  - envoi d'une image (glisser-déposer ou sélection) ;
  - analyse continue du flux de la **caméra** ;
  - affichage de l'image annotée, des détections (label, confiance, zone) ;
  - journal des **alertes** `DETECTION_ALERT`.
- Indicateur d'état de l'API (`/api/health/`).

## Lancer en local

Prérequis : Node.js 20+ et le backend lancé sur `http://localhost:8000`
(`python manage.py runserver` dans `moramora_api/`).

```bash
cd frontend
cp .env.example .env      # VITE_API_URL=http://localhost:8000
npm install
npm run dev               # http://localhost:5173
```

Le port `5173` est déjà autorisé par défaut côté Django
(`CORS_ALLOWED_ORIGINS` et `WEBSOCKET_ALLOWED_ORIGINS`).

## Fonctionnement de l'envoi d'images

- Les images sont redimensionnées (1280 px max) et encodées en JPEG dans le
  navigateur, puis envoyées en **trame binaire** (plus léger que le base64).
- **Une seule image en vol** : la suivante part après la réponse du serveur,
  qui n'exécute qu'une inférence à la fois (sinon il répond `busy`).
- La caméra nécessite `localhost` ou HTTPS.

## Déploiement Render

Le service `moramora-frontend` est déclaré dans `render.yaml` (site statique,
racine `frontend/`, build `npm ci && npm run build`, publication `dist/`).

1. Renseigner `VITE_API_URL` = URL du backend (ex. `https://moramora-api.onrender.com`).
2. Sur le backend, ajouter l'URL du frontend
   (ex. `https://moramora-frontend.onrender.com`) dans `CORS_ALLOWED_ORIGINS`
   **et** `WEBSOCKET_ALLOWED_ORIGINS`.

`VITE_API_URL` est intégrée au moment du build : redéployer le frontend après
l'avoir modifiée.

## Structure

```
src/
├── api.js                    # Client REST + URL WebSocket dérivée de VITE_API_URL
├── auth.jsx                  # Contexte d'authentification (token, profil)
├── hooks/useDetectionSocket.js  # Connexion WS, envoi, résultats, alertes
├── imageUtils.js             # Redimensionnement / JPEG, formatage
├── components/
│   ├── AuthScreen.jsx        # Connexion / inscription
│   ├── Header.jsx            # Barre du haut, état de l'API, déconnexion
│   ├── DetectionPanel.jsx    # Source image ou caméra
│   ├── ResultView.jsx        # Image annotée + tableau des détections
│   └── Sidebar.jsx           # Alertes + profil
├── App.jsx
├── main.jsx
└── styles.css                # Thème clair / sombre automatique
```
