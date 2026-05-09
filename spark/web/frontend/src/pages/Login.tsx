import { FormEvent, useState } from "react";
import { Zap } from "lucide-react";
import { api } from "../lib/api";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.post("/api/auth/login", { username, password });
      window.location.href = "/";
    } catch (err) {
      setError((err as Error).message || "login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <form onSubmit={onSubmit} className="panel p-6 w-96 space-y-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-5 h-5 rounded bg-gradient-to-br from-amber-400 to-amber-500 flex items-center justify-center shadow-sm">
            <Zap
              className="w-3.5 h-3.5 text-amber-950"
              strokeWidth={2.75}
              fill="currentColor"
              aria-hidden="true"
            />
          </div>
          <h1 className="font-bold text-xl">Spark</h1>
        </div>
        <p className="text-sm text-spark-muted">
          Credentials were printed to the console on startup. If you lost them,
          restart <span className="kbd">spark serve</span> with{" "}
          <span className="kbd">--rotate-credentials</span>.
        </p>
        <label className="block">
          <span className="label">Username</span>
          <input
            className="input w-full mt-1 font-mono"
            value={username}
            autoFocus
            autoComplete="username"
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label className="block">
          <span className="label">Password</span>
          <input
            className="input w-full mt-1 font-mono"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <div className="text-spark-danger text-sm">{error}</div>}
        <button
          type="submit"
          className="btn btn-primary w-full"
          disabled={loading}
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
