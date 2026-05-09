import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { Zap } from "lucide-react";
import { api } from "../lib/api";
export default function Login() {
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(false);
    async function onSubmit(e) {
        e.preventDefault();
        setError(null);
        setLoading(true);
        try {
            await api.post("/api/auth/login", { username, password });
            window.location.href = "/";
        }
        catch (err) {
            setError(err.message || "login failed");
        }
        finally {
            setLoading(false);
        }
    }
    return (_jsx("div", { className: "min-h-screen flex items-center justify-center", children: _jsxs("form", { onSubmit: onSubmit, className: "panel p-6 w-96 space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 mb-2", children: [_jsx("div", { className: "w-5 h-5 rounded bg-gradient-to-br from-amber-400 to-amber-500 flex items-center justify-center shadow-sm", children: _jsx(Zap, { className: "w-3.5 h-3.5 text-amber-950", strokeWidth: 2.75, fill: "currentColor", "aria-hidden": "true" }) }), _jsx("h1", { className: "font-bold text-xl", children: "Spark" })] }), _jsxs("p", { className: "text-sm text-spark-muted", children: ["Credentials were printed to the console on startup. If you lost them, restart ", _jsx("span", { className: "kbd", children: "spark serve" }), " with", " ", _jsx("span", { className: "kbd", children: "--rotate-credentials" }), "."] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Username" }), _jsx("input", { className: "input w-full mt-1 font-mono", value: username, autoFocus: true, autoComplete: "username", onChange: (e) => setUsername(e.target.value), required: true })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "label", children: "Password" }), _jsx("input", { className: "input w-full mt-1 font-mono", type: "password", autoComplete: "current-password", value: password, onChange: (e) => setPassword(e.target.value), required: true })] }), error && _jsx("div", { className: "text-spark-danger text-sm", children: error }), _jsx("button", { type: "submit", className: "btn btn-primary w-full", disabled: loading, children: loading ? "Signing in…" : "Sign in" })] }) }));
}
