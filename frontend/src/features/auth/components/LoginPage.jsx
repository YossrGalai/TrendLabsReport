import { useState } from "react";
import { FileSpreadsheet, Loader2 } from "lucide-react";
import { useAuth } from "../context/AuthContext";

const inputClass =
  "w-full rounded-lg border border-border bg-white px-3.5 py-3 text-foreground font-body text-sm focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary disabled:bg-muted disabled:text-muted-foreground transition-colors";
const labelClass = "block text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2";

export function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setErrorMsg(null);
    setIsSubmitting(true);
    try {
      await login(email, password);
      // navigate("/") est déjà fait dans AuthContext.login() en cas de succès.
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Échec de la connexion");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-md">
        <div className="flex flex-col items-center mb-8">
          <div className="flex size-14 items-center justify-center rounded-xl bg-primary text-primary-foreground mb-4">
            <FileSpreadsheet className="size-6" />
          </div>
          <p className="text-base font-bold tracking-tight text-foreground">TrendLabs</p>
          <p className="text-sm text-muted-foreground">Reporting Suite</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-card border border-border rounded-2xl shadow-[var(--shadow-card)] p-10 space-y-6"
        >
          <div>
            <label className={labelClass}>Email</label>
            <input
              type="email"
              className={inputClass}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
              required
              disabled={isSubmitting}
            />
          </div>
          <div>
            <label className={labelClass}>Mot de passe</label>
            <input
              type="password"
              className={inputClass}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={isSubmitting}
            />
          </div>

          {errorMsg && (
            <p className="text-destructive text-sm border border-destructive/20 bg-destructive/5 rounded-lg px-3 py-2">
              {errorMsg}
            </p>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full bg-primary hover:bg-primary-hover text-primary-foreground font-medium text-sm px-5 py-3 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
          >
            {isSubmitting && <Loader2 size={16} className="animate-spin" />}
            Se connecter
          </button>
        </form>
      </div>
    </div>
  );
}