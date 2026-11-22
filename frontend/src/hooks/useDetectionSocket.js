import { useCallback, useEffect, useRef, useState } from "react";
import { api, WS_URL } from "../api.js";

const MAX_IMAGE_BYTES = 5 * 1024 * 1024; // WEBSOCKET_MAX_IMAGE_SIZE côté serveur
const MAX_ALERTS = 30;
// Le premier appel charge le modèle YOLO : on laisse une marge confortable.
const RESPONSE_TIMEOUT_MS = 60_000;

/**
 * Gère la connexion à /ws/detection/.
 *
 * - Authentification par sous-protocole ["bearer", token].
 * - Une seule image en vol : le serveur répond "busy" s'il est occupé,
 *   on attend donc la réponse avant d'envoyer l'image suivante.
 * - Les images sont envoyées en trames binaires (JPEG), plus léger que le base64.
 */
export function useDetectionSocket(token, { onUnauthorized } = {}) {
  const wsRef = useRef(null);
  const pendingRef = useRef(false);
  const sentAtRef = useRef(0);
  const timeoutRef = useRef(null);
  const onUnauthorizedRef = useRef(onUnauthorized);

  const [status, setStatus] = useState("closed"); // closed | connecting | open
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [error, setError] = useState(null);
  const [stats, setStats] = useState({ sent: 0, processed: 0, busy: 0 });

  useEffect(() => {
    onUnauthorizedRef.current = onUnauthorized;
  });

  const setPendingState = useCallback((value) => {
    pendingRef.current = value;
    setPending(value);
    clearTimeout(timeoutRef.current);
  }, []);

  const connect = useCallback(() => {
    if (!token) return;
    const current = wsRef.current;
    if (current && current.readyState <= WebSocket.OPEN) return;

    setError(null);
    setStatus("connecting");
    let opened = false;
    const ws = new WebSocket(WS_URL, ["bearer", token]);
    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      opened = true;
      setStatus("open");
    };

    ws.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type === "detection_result") {
        setPendingState(false);
        setResult({
          ...message.data,
          latencyMs: Math.round(performance.now() - sentAtRef.current),
          receivedAt: new Date(),
        });
        setStats((s) => ({ ...s, processed: s.processed + 1 }));
      } else if (message.type === "alert") {
        const entry = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          at: new Date(),
          message: message.message,
          detections: message.data?.detections ?? [],
        };
        setAlerts((list) => [entry, ...list].slice(0, MAX_ALERTS));
      } else if (message.type === "error") {
        setPendingState(false);
        if (message.code === "busy") {
          setStats((s) => ({ ...s, busy: s.busy + 1 }));
        } else {
          setError(message.message);
        }
      }
    };

    ws.onclose = (event) => {
      if (wsRef.current !== ws) return;
      wsRef.current = null;
      setPendingState(false);
      setStatus("closed");

      if (event.code === 4401) {
        setError("Session invalide : reconnectez-vous.");
        onUnauthorizedRef.current?.();
      } else if (!opened) {
        // Le serveur refuse la poignée de main avant acceptation : le navigateur
        // ne voit qu'un code 1006. On distingue token invalide / origine refusée.
        api
          .profile(token)
          .then(() =>
            setError(
              "Connexion WebSocket refusée. Vérifiez que l'origine du frontend " +
                "figure dans WEBSOCKET_ALLOWED_ORIGINS et que le backend tourne.",
            ),
          )
          .catch((err) => {
            if (err.status === 401) {
              setError("Session invalide : reconnectez-vous.");
              onUnauthorizedRef.current?.();
            } else {
              setError(err.message);
            }
          });
      } else if (event.code !== 1000) {
        setError(`Connexion interrompue (code ${event.code}).`);
      }
    };
  }, [token, setPendingState]);

  const disconnect = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    setPendingState(false);
    setStatus("closed");
    ws?.close(1000);
  }, [setPendingState]);

  const canSend = useCallback(
    () => wsRef.current?.readyState === WebSocket.OPEN && !pendingRef.current,
    [],
  );

  const send = useCallback(
    (blob) => {
      if (!canSend()) return false;
      if (blob.size > MAX_IMAGE_BYTES) {
        setError("Image trop lourde (5 Mio maximum).");
        return false;
      }
      setError(null);
      setPendingState(true);
      sentAtRef.current = performance.now();
      timeoutRef.current = setTimeout(() => {
        pendingRef.current = false;
        setPending(false);
        setError("Pas de réponse du serveur : réessayez.");
      }, RESPONSE_TIMEOUT_MS);
      wsRef.current.send(blob);
      setStats((s) => ({ ...s, sent: s.sent + 1 }));
      return true;
    },
    [canSend, setPendingState],
  );

  const clearAlerts = useCallback(() => setAlerts([]), []);

  // Déconnexion à la perte du token et au démontage.
  useEffect(() => {
    if (!token) disconnect();
  }, [token, disconnect]);
  useEffect(
    () => () => {
      clearTimeout(timeoutRef.current);
      const ws = wsRef.current;
      wsRef.current = null;
      ws?.close(1000);
    },
    [],
  );

  return {
    status,
    pending,
    result,
    alerts,
    error,
    stats,
    connect,
    disconnect,
    canSend,
    send,
    clearAlerts,
  };
}
