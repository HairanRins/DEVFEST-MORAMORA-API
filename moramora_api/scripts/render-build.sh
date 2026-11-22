#!/usr/bin/env bash
# Build Render : dépendances (torch CPU), fichiers statiques, préchargement YOLO.
# Exécuté depuis la racine du dépôt.
set -o errexit -o nounset -o pipefail

python -m pip install --upgrade pip

# 1. PyTorch CPU uniquement (Render n'a pas de GPU ; évite les paquets CUDA).
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 2. Dépendances du projet.
pip install -r moramora_api/requirements.txt

# 3. ultralytics tire opencv-python (besoin de libGL) : on garde la version headless.
pip uninstall -y opencv-python || true
pip install --force-reinstall --no-deps "opencv-python-headless>=4.10,<6.0"

# 4. Fichiers statiques servis par WhiteNoise (admin, DRF, etc.).
python moramora_api/manage.py collectstatic --noinput

# 5. Téléchargement des poids Hugging Face + police d'annotation Ultralytics
#    pendant le build (HF_HOME / YOLO_CONFIG_DIR pointent dans le projet,
#    seul répertoire conservé entre le build et l'exécution).
python - <<'PY'
import numpy as np
from violation.inference import detect_image

image, detections = detect_image(np.zeros((64, 64, 3), dtype=np.uint8))
print("YOLO prêt :", image.shape, len(detections), "détection(s)")
PY
