import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";

interface AuthState {
  authed: boolean;
  loading: boolean;
  subject: string | null;
  role: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const CHECK_INTERVAL_MS = 30_000; // re-validate session every 30s

export function useAuth(): AuthState {
  const [authed, setAuthed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [subject, setSubject] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const checkSession = useCallback(async () => {
    try {
      const me = await api.get<{ subject: string; role: string }>("/api/auth/me");
      setSubject(me.subject);
      setRole(me.role);
      setAuthed(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setAuthed(false);
        setSubject(null);
        setRole(null);
      }
    }
  }, []);

  // Initial check + periodic re-validation.
  useEffect(() => {
    (async () => {
      await checkSession();
      setLoading(false);
    })();

    intervalRef.current = setInterval(checkSession, CHECK_INTERVAL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [checkSession]);

  const login = async (username: string, password: string) => {
    const resp = await api.post<{ subject: string; role: string }>("/api/auth/login", {
      username,
      password,
    });
    setSubject(resp.subject);
    setRole(resp.role);
    setAuthed(true);
  };

  const logout = async () => {
    await api.post("/api/auth/logout");
    // Hard redirect — don't wait for React state propagation.
    window.location.href = "/login";
  };

  return { authed, loading, subject, role, login, logout };
}
