// Préparation des images avant envoi : redimensionnement + JPEG.
// Réduit la taille des trames et respecte la limite serveur (5 Mio / 20 MP).

const MAX_SIDE = 1280;
const JPEG_QUALITY = 0.85;

export function drawToJpeg(source, width, height) {
  const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
}

export async function fileToJpeg(file) {
  const bitmap = await createImageBitmap(file);
  try {
    return await drawToJpeg(bitmap, bitmap.width, bitmap.height);
  } finally {
    bitmap.close();
  }
}

export function formatPercent(value) {
  return `${Math.round(value * 100)} %`;
}

export function formatTime(date) {
  return date.toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
