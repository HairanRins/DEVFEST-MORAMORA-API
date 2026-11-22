// Client REST pour l'API Django MoraMora.

export const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(
  /\/+$/,
  "",
);

// http://hote -> ws://hote/ws/detection/ ; https://hote -> wss://hote/ws/detection/
export const WS_URL = `${API_URL.replace(/^http/, "ws")}/ws/detection/`;

export class ApiError extends Error {
  constructor(message, status, errors = null) {
    super(message);
    this.status = status;
    this.errors = errors;
  }
}

async function request(path, { method = "GET", body, token } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(`Serveur injoignable (${API_URL}).`, 0);
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // Réponse vide ou non JSON.
  }

  if (!response.ok) {
    // Format d'erreur du backend : {success:false, message, errors}
    throw new ApiError(
      payload?.message || `Erreur HTTP ${response.status}`,
      response.status,
      payload?.errors ?? null,
    );
  }
  return payload;
}

export const api = {
  health: () => request("/api/health/"),
  signup: (data) => request("/api/auth/signup/", { method: "POST", body: data }),
  login: (email, password) =>
    request("/api/auth/login/", { method: "POST", body: { email, password } }),
  profile: (token) => request("/api/auth/profile/", { token }),
  logout: (token) => request("/api/auth/logout/", { method: "POST", token }),
};

/** Transforme `errors` du backend en { champ: "message" }. */
export function fieldErrors(errors) {
  if (!errors || typeof errors !== "object") return {};
  return Object.fromEntries(
    Object.entries(errors).map(([field, value]) => [
      field,
      Array.isArray(value) ? value.join(" ") : String(value),
    ]),
  );
}
