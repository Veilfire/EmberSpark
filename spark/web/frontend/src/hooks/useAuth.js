import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
const CHECK_INTERVAL_MS = 30_000; // re-validate session every 30s
export function useAuth() {
    const [authed, setAuthed] = useState(false);
    const [loading, setLoading] = useState(true);
    const [subject, setSubject] = useState(null);
    const [role, setRole] = useState(null);
    const intervalRef = useRef(null);
    const checkSession = useCallback(async () => {
        try {
            const me = await api.get("/api/auth/me");
            setSubject(me.subject);
            setRole(me.role);
            setAuthed(true);
        }
        catch (e) {
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
            if (intervalRef.current)
                clearInterval(intervalRef.current);
        };
    }, [checkSession]);
    const login = async (username, password) => {
        const resp = await api.post("/api/auth/login", {
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
