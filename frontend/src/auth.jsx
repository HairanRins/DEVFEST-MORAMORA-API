import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";

const TOKEN_KEY = "moramora.access";

function readToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Stockage indisponible (navigation privée…) : session en mémoire seulement.
  }
}

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(readToken);
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(() => Boolean(readToken()));

  const clearSession = useCallback(() => {
    writeToken(null);
    setToken(null);
    setProfile(null);
  }, []);

  // Charge le profil dès qu'un token est disponible ; un 401 = token révoqué.
  useEffect(() => {
    if (!token) {
      setLoading(false);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    api
      .profile(token)
      .then((response) => !cancelled && setProfile(response.data))
      .catch((error) => !cancelled && error.status === 401 && clearSession())
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [token, clearSession]);

  const login = useCallback(async (email, password) => {
    const response = await api.login(email, password);
    writeToken(response.data.access);
    setToken(response.data.access);
    return response;
  }, []);

  const signup = useCallback(
    async (form) => {
      await api.signup(form);
      return login(form.email, form.password);
    },
    [login],
  );

  const logout = useCallback(async () => {
    if (token) {
      try {
        await api.logout(token);
      } catch {
        // Token déjà invalide ou serveur injoignable : on nettoie quand même.
      }
    }
    clearSession();
  }, [token, clearSession]);

  const value = useMemo(
    () => ({ token, profile, loading, login, signup, logout, clearSession }),
    [token, profile, loading, login, signup, logout, clearSession],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth doit être utilisé dans <AuthProvider>.");
  return context;
}
