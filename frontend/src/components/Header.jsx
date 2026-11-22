import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";

const HEALTH_INTERVAL_MS = 30_000;

function useApiHealth() {
  const [healthy, setHealthy] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const check = () =>
      api
        .health()
        .then(() => !cancelled && setHealthy(true))
        .catch(() => !cancelled && setHealthy(false));
    check();
    const timer = setInterval(check, HEALTH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);
  return healthy;
}

export default function Header() {
  const { profile, logout } = useAuth();
  const healthy = useApiHealth();
  const [leaving, setLeaving] = useState(false);

  const label = healthy === null ? "API…" : healthy ? "API en ligne" : "API hors ligne";
  const tone = healthy === null ? "neutral" : healthy ? "ok" : "danger";

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">M</span>
        MoraMora
      </div>
      <div className="topbar-right">
        <span className={`pill pill-${tone}`}>
          <span className="dot" aria-hidden="true" />
          {label}
        </span>
        {profile && <span className="muted hide-sm">{profile.username}</span>}
        <button
          className="btn btn-ghost"
          disabled={leaving}
          onClick={async () => {
            setLeaving(true);
            await logout();
          }}
        >
          Déconnexion
        </button>
      </div>
    </header>
  );
}
