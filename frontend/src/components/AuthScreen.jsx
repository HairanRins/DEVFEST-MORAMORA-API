import { useState } from "react";
import { API_URL, fieldErrors } from "../api.js";
import { useAuth } from "../auth.jsx";

const EMPTY_FORM = { username: "", email: "", password: "", password_confirmation: "" };

function Field({ label, name, type = "text", value, onChange, error, autoComplete }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        name={name}
        type={type}
        value={value}
        onChange={onChange}
        autoComplete={autoComplete}
        aria-invalid={Boolean(error)}
        required
      />
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}

export default function AuthScreen() {
  const { login, signup } = useAuth();
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const isSignup = mode === "signup";

  const update = (event) =>
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }));

  const switchMode = (next) => {
    setMode(next);
    setErrors({});
    setMessage(null);
  };

  const submit = async (event) => {
    event.preventDefault();
    setErrors({});
    setMessage(null);
    setSubmitting(true);
    try {
      if (isSignup) await signup(form);
      else await login(form.email, form.password);
    } catch (error) {
      const byField = fieldErrors(error.errors);
      setErrors(byField);
      setMessage(byField.detail || byField.non_field_errors || error.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-layout">
      <section className="auth-intro">
        <div className="brand brand-large">
          <span className="brand-mark" aria-hidden="true">M</span>
          MoraMora
        </div>
        <h1>Détection d'infractions en temps réel</h1>
        <p>
          Envoyez une image ou le flux de votre caméra : le modèle YOLOv8 repère les
          infractions et vous alerte immédiatement.
        </p>
        <ul className="feature-list">
          <li>Analyse d'images JPEG / PNG</li>
          <li>Flux caméra en continu</li>
          <li>Alertes instantanées par WebSocket</li>
        </ul>
      </section>

      <section className="card auth-card">
        <div className="tabs" role="tablist">
          <button
            role="tab"
            aria-selected={!isSignup}
            className={!isSignup ? "active" : ""}
            onClick={() => switchMode("login")}
          >
            Connexion
          </button>
          <button
            role="tab"
            aria-selected={isSignup}
            className={isSignup ? "active" : ""}
            onClick={() => switchMode("signup")}
          >
            Inscription
          </button>
        </div>

        <form onSubmit={submit} className="form">
          {isSignup && (
            <Field
              label="Nom d'utilisateur"
              name="username"
              value={form.username}
              onChange={update}
              error={errors.username}
              autoComplete="username"
            />
          )}
          <Field
            label="Email"
            name="email"
            type="email"
            value={form.email}
            onChange={update}
            error={errors.email}
            autoComplete="email"
          />
          <Field
            label="Mot de passe"
            name="password"
            type="password"
            value={form.password}
            onChange={update}
            error={errors.password}
            autoComplete={isSignup ? "new-password" : "current-password"}
          />
          {isSignup && (
            <Field
              label="Confirmation du mot de passe"
              name="password_confirmation"
              type="password"
              value={form.password_confirmation}
              onChange={update}
              error={errors.password_confirmation}
              autoComplete="new-password"
            />
          )}

          {message && <p className="banner banner-error">{message}</p>}

          <button className="btn btn-primary" disabled={submitting}>
            {submitting ? "Patientez…" : isSignup ? "Créer mon compte" : "Se connecter"}
          </button>
        </form>
        <p className="muted small">API : {API_URL}</p>
      </section>
    </main>
  );
}
