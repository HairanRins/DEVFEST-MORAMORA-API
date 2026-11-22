# Déploiement sur Render (sans Docker, sans Redis)

Le service tourne en **runtime Python natif** Render, avec **Daphne** (ASGI :
HTTP + WebSocket sur le même port), le **channel layer en mémoire** et la base
**Neon PostgreSQL**. Aucun conteneur ni Redis n'est nécessaire.

## Fichiers ajoutés

| Fichier | Rôle |
| --- | --- |
| `render.yaml` | Blueprint Render (service web, variables d'env, health check) |
| `moramora_api/scripts/render-build.sh` | Installe torch **CPU**, les dépendances, `collectstatic`, précharge le modèle YOLO |
| `moramora_api/scripts/render-start.sh` | `migrate` puis `daphne --proxy-headers` sur `$PORT` |
| `moramora_api/moramora_api/openapi_settings.py` | Configuration OpenAPI / Swagger |
| `moramora_api/authentication/openapi.py` | Schémas des réponses + sécurité Bearer |
| `docs/openapi.yaml` | Export statique du schéma OpenAPI 3 |

## Étapes

1. **Sécuriser les secrets** : `moramora_api/.env` a été poussé sur GitHub.
   Régénérer le mot de passe Neon, les clés S3 et le secret Realtime, puis
   committer la suppression du fichier du suivi git (il reste en local).
2. Pousser la branche `main` sur GitHub.
3. Render → **New → Blueprint** → choisir le dépôt. Render lit `render.yaml`.
4. Renseigner les variables marquées `sync: false` :
   - `DATABASE_URL` : URL Neon **directe** (garder
     `?sslmode=require&channel_binding=require`).
   - `CORS_ALLOWED_ORIGINS` : URL(s) du frontend, ex. `https://moramora.vercel.app`.
   - `WEBSOCKET_ALLOWED_ORIGINS` : mêmes origines que le frontend.
5. Lancer le déploiement. Le premier build prend plusieurs minutes (PyTorch +
   poids YOLO).
6. Vérifier :
   - `https://<service>.onrender.com/api/health/` → `{"status":"ok"}`
   - `https://<service>.onrender.com/api/docs/` → Swagger UI
   - WebSocket : `wss://<service>.onrender.com/ws/detection/`

`DJANGO_SECRET_KEY` est générée par Render ; `ALLOWED_HOSTS` et
`CSRF_TRUSTED_ORIGINS` incluent automatiquement `RENDER_EXTERNAL_HOSTNAME`.
Pour un domaine personnalisé, l'ajouter à `DJANGO_ALLOWED_HOSTS`.

## Points d'attention

- **Mémoire** : PyTorch + YOLOv8 dépassent les 512 Mo des plans Free/Starter.
  Le blueprint utilise `standard` (2 Go). Le plan Free met aussi le service en
  veille après 15 min d'inactivité (premier appel lent, WebSocket coupé).
- **Une seule instance / un seul processus** : le channel layer en mémoire et
  le verrou d'inférence (`busy`) sont locaux au processus. Ne pas augmenter
  `numInstances` sans passer à un channel layer partagé.
- **Migrations** : exécutées au démarrage par `render-start.sh` via l'URL
  directe, ce qui est sûr avec une seule instance. Ne pas activer
  `DJANGO_DB_USE_POOLED` pour les migrations.
- **Poids du modèle** : téléchargés au build dans `.cache/` du projet
  (`HF_HOME`, `YOLO_CONFIG_DIR`), donc pas de téléchargement au premier appel.

## Documentation de l'API

| URL | Contenu |
| --- | --- |
| `/api/docs/` | Swagger UI interactif (bouton **Authorize** → coller le JWT) |
| `/api/redoc/` | ReDoc |
| `/api/schema/` | Schéma OpenAPI 3 (YAML ; `?format=json` pour JSON) |

Le WebSocket n'est pas descriptible en OpenAPI 3 : il est documenté dans
l'en-tête de la page Swagger, et ses messages (`WsImageMessage`,
`WsDetectionResult`, `WsAlert`, `WsError`) figurent dans la section **Schemas**.

Régénérer l'export statique après une modification d'API :

```bash
cd moramora_api
python manage.py spectacular --file ../docs/openapi.yaml --validate
```
