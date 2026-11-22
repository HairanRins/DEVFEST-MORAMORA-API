import { useCallback, useEffect, useRef, useState } from "react";
import { drawToJpeg, fileToJpeg } from "../imageUtils.js";

const FRAME_INTERVAL_MS = 400;

const STATUS_LABELS = {
  open: { text: "Connecté", tone: "ok" },
  connecting: { text: "Connexion…", tone: "neutral" },
  closed: { text: "Déconnecté", tone: "danger" },
};

function ImageSource({ socket }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState(null);

  useEffect(() => () => preview && URL.revokeObjectURL(preview), [preview]);

  const choose = (selected) => {
    if (!selected) return;
    if (!selected.type.startsWith("image/")) {
      setLocalError("Choisissez un fichier image.");
      return;
    }
    setLocalError(null);
    setFile(selected);
    setPreview(URL.createObjectURL(selected));
  };

  const analyse = async () => {
    try {
      const blob = await fileToJpeg(file);
      socket.send(blob);
    } catch {
      setLocalError("Image illisible par le navigateur.");
    }
  };

  return (
    <div className="source">
      <label
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          choose(event.dataTransfer.files?.[0]);
        }}
      >
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp"
          onChange={(event) => choose(event.target.files?.[0])}
          hidden
        />
        {preview ? (
          <img src={preview} alt="Image sélectionnée" className="preview" />
        ) : (
          <span>
            <strong>Glissez une image ici</strong>
            <br />
            ou cliquez pour parcourir (JPEG, PNG)
          </span>
        )}
      </label>
      {localError && <p className="banner banner-error">{localError}</p>}
      <button
        className="btn btn-primary"
        disabled={!file || socket.status !== "open" || socket.pending}
        onClick={analyse}
      >
        {socket.pending ? "Analyse en cours…" : "Analyser l'image"}
      </button>
    </div>
  );
}

function CameraSource({ socket }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const [cameraOn, setCameraOn] = useState(false);
  const [live, setLive] = useState(false);
  const [cameraError, setCameraError] = useState(null);
  const { canSend, send } = socket;

  const stopCamera = useCallback(() => {
    setLive(false);
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setCameraOn(false);
  }, []);

  const startCamera = async () => {
    setCameraError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      streamRef.current = stream;
      videoRef.current.srcObject = stream;
      await videoRef.current.play();
      setCameraOn(true);
    } catch (error) {
      setCameraError(
        error?.name === "NotAllowedError"
          ? "Accès à la caméra refusé."
          : "Caméra indisponible (HTTPS ou localhost requis).",
      );
    }
  };

  useEffect(() => stopCamera, [stopCamera]);

  // Boucle d'analyse : une image à la fois, la suivante après la réponse.
  useEffect(() => {
    if (!live) return undefined;
    let stopped = false;
    let timer;
    const tick = async () => {
      const video = videoRef.current;
      if (!stopped && canSend() && video?.readyState >= 2) {
        const blob = await drawToJpeg(video, video.videoWidth, video.videoHeight);
        if (blob && !stopped) send(blob);
      }
      if (!stopped) timer = setTimeout(tick, FRAME_INTERVAL_MS);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [live, canSend, send]);

  useEffect(() => {
    if (socket.status !== "open") setLive(false);
  }, [socket.status]);

  return (
    <div className="source">
      <div className="video-frame">
        <video ref={videoRef} muted playsInline className={cameraOn ? "" : "hidden"} />
        {!cameraOn && <span className="muted">Caméra éteinte</span>}
        {live && <span className="live-badge">● EN DIRECT</span>}
      </div>
      {cameraError && <p className="banner banner-error">{cameraError}</p>}
      <div className="row">
        {cameraOn ? (
          <button className="btn btn-ghost" onClick={stopCamera}>
            Éteindre la caméra
          </button>
        ) : (
          <button className="btn btn-ghost" onClick={startCamera}>
            Allumer la caméra
          </button>
        )}
        <button
          className={`btn ${live ? "btn-danger" : "btn-primary"}`}
          disabled={!cameraOn || socket.status !== "open"}
          onClick={() => setLive((value) => !value)}
        >
          {live ? "Arrêter l'analyse" : "Analyse en continu"}
        </button>
      </div>
    </div>
  );
}

export default function DetectionPanel({ socket }) {
  const [source, setSource] = useState("image");
  const status = STATUS_LABELS[socket.status];

  return (
    <section className="card">
      <div className="card-head">
        <h2>Source</h2>
        <div className="row">
          <span className={`pill pill-${status.tone}`}>
            <span className="dot" aria-hidden="true" />
            {status.text}
          </span>
          {socket.status === "closed" ? (
            <button className="btn btn-small" onClick={socket.connect}>
              Se connecter
            </button>
          ) : (
            <button className="btn btn-small btn-ghost" onClick={socket.disconnect}>
              Couper
            </button>
          )}
        </div>
      </div>

      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={source === "image"}
          className={source === "image" ? "active" : ""}
          onClick={() => setSource("image")}
        >
          Image
        </button>
        <button
          role="tab"
          aria-selected={source === "camera"}
          className={source === "camera" ? "active" : ""}
          onClick={() => setSource("camera")}
        >
          Caméra
        </button>
      </div>

      {source === "image" ? <ImageSource socket={socket} /> : <CameraSource socket={socket} />}

      {socket.error && <p className="banner banner-error">{socket.error}</p>}
      <p className="muted small">
        Envoyées : {socket.stats.sent} · Traitées : {socket.stats.processed} · Serveur
        occupé : {socket.stats.busy}
      </p>
    </section>
  );
}
