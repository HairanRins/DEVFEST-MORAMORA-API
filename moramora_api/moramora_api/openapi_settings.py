"""OpenAPI (drf-spectacular) configuration.

OpenAPI 3 cannot describe WebSocket endpoints, so the detection WebSocket is
documented in the API description and its message formats are published as
reusable components (``WsImageMessage``, ``WsDetectionResult``…).
"""

import os

_DESCRIPTION = """
API **MoraMora** : authentification JWT (REST) et détection d'infractions
YOLOv8, en REST (`POST /api/detection/`, testable ici) ou en temps réel via
WebSocket (`/ws/detection/`, non testable dans Swagger : voir ci-dessous).

## Authentification

1. `POST /api/auth/signup/` pour créer un compte.
2. `POST /api/auth/login/` renvoie `data.access` (JWT HS256 **sans expiration**).
3. Envoyer `Authorization: Bearer <JWT>` sur les routes protégées
   (bouton **Authorize** ci-dessus : coller uniquement le token).
4. `POST /api/auth/logout/` révoque immédiatement le token.

Les erreurs suivent toujours le format
`{"success": false, "message": "...", "errors": {...}}`.

## WebSocket de détection — `/ws/detection/`

URL : `wss://<hôte>/ws/detection/` (`ws://localhost:8000/ws/detection/` en local).

**Connexion**

- Navigateur : `new WebSocket(url, ["bearer", accessToken])` — le serveur
  sélectionne le sous-protocole `bearer`.
- Autre client : en-tête `Authorization: Bearer <JWT>`.
- Un en-tête `Origin` présent dans `WEBSOCKET_ALLOWED_ORIGINS` est obligatoire.
- Le JWT dans l'URL (query string) est refusé.

| Code de fermeture | Cause |
| --- | --- |
| `4401` | JWT absent, invalide, révoqué ou compte inactif |
| `4403` | En-tête `Origin` absent |
| `403` (handshake refusé) | Origine non autorisée |

**Client → serveur** (une image par message)

- trame binaire contenant un JPEG ou PNG brut ;
- ou JSON `WsImageMessage` : `{"type": "image", "image": "<base64 ou data URL>"}`.

Limites : 5 Mio et 20 mégapixels par image (configurables).

**Serveur → client**

- `WsDetectionResult` : image annotée (JPEG base64) + détections ;
- `WsAlert` : envoyé juste après le résultat si au moins une détection ;
- `WsError` : `code` = `invalid_image` | `busy` | `processing_error`.
  `busy` signifie qu'une inférence est déjà en cours : réessayer.

Les schémas de ces messages sont listés dans la section **Schemas** en bas de page.

```javascript
const socket = new WebSocket("wss://<hôte>/ws/detection/", ["bearer", accessToken]);
socket.onopen = () => socket.send(JSON.stringify({ type: "image", image: dataUrl }));
socket.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  if (msg.type === "detection_result") show(msg.data.image, msg.data.detections);
  if (msg.type === "alert") notify(msg.data.detections);
  if (msg.type === "error") console.warn(msg.code, msg.message);
};
```
"""

_BBOX = {
    "type": "object",
    "properties": {
        "x1": {"type": "number", "format": "float"},
        "y1": {"type": "number", "format": "float"},
        "x2": {"type": "number", "format": "float"},
        "y2": {"type": "number", "format": "float"},
    },
    "required": ["x1", "y1", "x2", "y2"],
}

_DETECTION = {
    "type": "object",
    "properties": {
        "class_id": {"type": "integer", "example": 0},
        "label": {"type": "string", "example": "no_helmet"},
        "confidence": {"type": "number", "format": "float", "example": 0.87},
        "bbox": {"$ref": "#/components/schemas/WsBoundingBox"},
    },
    "required": ["class_id", "label", "confidence", "bbox"],
}

_WEBSOCKET_COMPONENTS = {
    "schemas": {
        "WsBoundingBox": _BBOX,
        "WsDetection": _DETECTION,
        "WsImageMessage": {
            "type": "object",
            "description": "Message client → serveur (alternative : trame binaire JPEG/PNG).",
            "properties": {
                "type": {"type": "string", "enum": ["image"]},
                "image": {
                    "type": "string",
                    "description": "Image JPEG/PNG en base64, ou data URL "
                    "`data:image/jpeg;base64,...`.",
                },
            },
            "required": ["type", "image"],
        },
        "WsDetectionResult": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["detection_result"]},
                "success": {"type": "boolean", "enum": [True]},
                "data": {
                    "type": "object",
                    "properties": {
                        "image": {
                            "type": "string",
                            "description": "Image annotée, JPEG encodé en base64.",
                        },
                        "detections": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/WsDetection"},
                        },
                        "message": {
                            "type": "string",
                            "example": "Image processed successfully",
                        },
                    },
                    "required": ["image", "detections", "message"],
                },
            },
            "required": ["type", "success", "data"],
        },
        "WsAlert": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["alert"]},
                "event": {"type": "string", "enum": ["DETECTION_ALERT"]},
                "message": {
                    "type": "string",
                    "example": "A violation has been detected",
                },
                "data": {
                    "type": "object",
                    "properties": {
                        "detections": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/WsDetection"},
                        }
                    },
                    "required": ["detections"],
                },
            },
            "required": ["type", "event", "message", "data"],
        },
        "WsError": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["error"]},
                "success": {"type": "boolean", "enum": [False]},
                "code": {
                    "type": "string",
                    "enum": ["invalid_image", "busy", "processing_error"],
                },
                "message": {"type": "string"},
            },
            "required": ["type", "success", "code", "message"],
        },
    }
}

SPECTACULAR_SETTINGS = {
    "TITLE": "MoraMora API",
    "DESCRIPTION": _DESCRIPTION,
    "VERSION": os.getenv("API_VERSION", "1.0.0"),
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/",
    "COMPONENT_SPLIT_REQUEST": True,
    "APPEND_COMPONENTS": _WEBSOCKET_COMPONENTS,
    "TAGS": [
        {"name": "Authentification", "description": "Comptes et JWT."},
        {
            "name": "Détection",
            "description": "Détection d'infractions YOLOv8 (REST). "
            "Le flux temps réel passe par le WebSocket /ws/detection/ "
            "décrit en haut de page.",
        },
        {"name": "Système", "description": "Supervision du service."},
    ],
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
        "displayRequestDuration": True,
    },
}
