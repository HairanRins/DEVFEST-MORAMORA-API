import { useAuth } from "../auth.jsx";
import { formatPercent, formatTime } from "../imageUtils.js";

function formatDate(value) {
  return value ? new Date(value).toLocaleString("fr-FR") : "—";
}

export function ProfileCard() {
  const { profile } = useAuth();
  return (
    <section className="card">
      <h2>Profil</h2>
      {profile ? (
        <dl className="profile">
          <dt>Utilisateur</dt>
          <dd>{profile.username}</dd>
          <dt>Email</dt>
          <dd>{profile.email}</dd>
          <dt>Inscrit le</dt>
          <dd>{formatDate(profile.date_joined)}</dd>
          <dt>Dernière connexion</dt>
          <dd>{formatDate(profile.last_login)}</dd>
        </dl>
      ) : (
        <p className="muted">Profil indisponible.</p>
      )}
    </section>
  );
}

export function AlertsCard({ alerts, onClear }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>
          Alertes {alerts.length > 0 && <span className="count">{alerts.length}</span>}
        </h2>
        {alerts.length > 0 && (
          <button className="btn btn-small btn-ghost" onClick={onClear}>
            Effacer
          </button>
        )}
      </div>
      {alerts.length === 0 ? (
        <p className="muted">Aucune alerte pour l'instant.</p>
      ) : (
        <ul className="alerts">
          {alerts.map((alert) => (
            <li key={alert.id} className="alert-item">
              <div className="alert-head">
                <strong>{alert.message}</strong>
                <span className="muted small">{formatTime(alert.at)}</span>
              </div>
              <div className="tags">
                {alert.detections.map((detection, index) => (
                  <span key={index} className="tag">
                    {detection.label} · {formatPercent(detection.confidence)}
                  </span>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
