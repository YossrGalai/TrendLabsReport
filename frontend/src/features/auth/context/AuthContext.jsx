import { createContext, useContext, useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { login as apiLogin, logout as apiLogout, fetchMe } from "../../../api/auth";
import { getToken, setUnauthorizedHandler } from "../../../api/http";

const AuthContext = createContext(null);

// Doit être monté À L'INTÉRIEUR de <BrowserRouter> (useNavigate en dépend) — cf. main.jsx,
// enveloppe <App /> à l'intérieur du <BrowserRouter> déjà présent.
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const navigate = useNavigate();

  // apiFetch (http.js) appelle ce handler sur tout 401/403 — expiration de token en cours
  // d'utilisation, pas seulement au chargement initial de l'app.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      navigate("/login");
    });
  }, [navigate]);

  // Au premier chargement : si un token existe déjà (session précédente, localStorage),
  // vérifier qu'il est toujours valide avant de considérer l'utilisateur connecté.
  useEffect(() => {
    if (!getToken()) {
      setIsLoading(false);
      return;
    }
    fetchMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setIsLoading(false));
  }, []);

  const login = async (email, password) => {
    await apiLogin(email, password);
    const me = await fetchMe();
    setUser(me);
    navigate("/");
  };

  const logout = () => {
    apiLogout();
    setUser(null);
    navigate("/login");
  };

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth doit être utilisé à l'intérieur de <AuthProvider>");
  return ctx;
}

// Enveloppe les routes protégées dans App.jsx — redirige vers /login si pas connecté,
// affiche un état de chargement pendant la vérification du token existant (évite un flash
// de redirection vers /login avant même d'avoir su si le token était valide).
export function RequireAuth({ children }) {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        Chargement…
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return children;
}