import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { login, signup } from "../api/auth";
import { UnauthorizedError, ApiError } from "../api/client";

export function LoginPage() {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const action = mode === "login" ? login : signup;
      const user = await action(email, password);
      queryClient.setQueryData(["me"], user);
      void navigate("/agents");
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        setError("Invalid email or password.");
      } else if (err instanceof ApiError) {
        if (err.status === 409) {
          setError("An account with this email already exists.");
        } else {
          setError(err.message || (mode === "login" ? "Login failed." : "Signup failed."));
        }
      } else {
        setError("Unexpected error. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(34,211,238,0.18),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.16),_transparent_30%),linear-gradient(180deg,_#020617_0%,_#0f172a_55%,_#111827_100%)] p-4">
      <div className="mx-auto flex min-h-screen max-w-6xl items-center justify-center">
        <div className="grid w-full overflow-hidden rounded-[2rem] border border-white/10 bg-slate-950/80 shadow-2xl shadow-cyan-950/30 backdrop-blur lg:grid-cols-[1.05fr_0.95fr]">
          <section className="hidden border-r border-white/10 bg-white/5 p-10 lg:flex lg:items-end">
            <div className="space-y-6">
              <p className="text-xs uppercase tracking-[0.3em] text-cyan-300">
                RF Platform
              </p>
              <div className="space-y-4">
                <h1 className="max-w-lg text-4xl font-semibold leading-tight text-white">
                  Watch live spectrum without digging through a cluttered UI.
                </h1>
                <p className="max-w-md text-base text-slate-300">
                  Sign in to open your agent dashboard, create tokens, and jump
                  straight into the live waterfall.
                </p>
              </div>
            </div>
          </section>

          <section className="p-6 sm:p-8 lg:p-10">
            <div className="mx-auto w-full max-w-md space-y-6">
              <div className="space-y-3">
                <Link
                  to="/"
                  className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-cyan-400/30 bg-cyan-400/10 text-lg font-semibold text-cyan-200 transition hover:border-cyan-300/50 hover:bg-cyan-400/20 hover:text-white"
                >
                  RF
                </Link>
                <div>
                  <h2 className="text-3xl font-semibold text-white">
                    {mode === "login" ? "Sign in" : "Create account"}
                  </h2>
                  <p className="mt-2 text-sm text-slate-400">
                    {mode === "login"
                      ? "Use your existing account to reach the dashboard."
                      : "Create your account and start managing agents right away."}
                  </p>
                </div>
              </div>

              <div className="inline-flex rounded-2xl border border-white/10 bg-white/5 p-1">
                <button
                  type="button"
                  onClick={() => {
                    setMode("login");
                    setError(null);
                  }}
                  className={`rounded-2xl px-4 py-2 text-sm transition ${
                    mode === "login"
                      ? "bg-cyan-400 text-slate-950"
                      : "text-slate-300 hover:text-white"
                  }`}
                >
                  Sign in
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMode("signup");
                    setError(null);
                  }}
                  className={`rounded-2xl px-4 py-2 text-sm transition ${
                    mode === "signup"
                      ? "bg-cyan-400 text-slate-950"
                      : "text-slate-300 hover:text-white"
                  }`}
                >
                  Sign up
                </button>
              </div>

              <form
                onSubmit={(e) => { void handleSubmit(e); }}
                className="space-y-5 rounded-[1.5rem] border border-white/10 bg-white/5 p-6"
              >
                {error && (
                  <p className="rounded-2xl border border-rose-400/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-200">
                    {error}
                  </p>
                )}

                <div className="space-y-2">
                  <label className="block text-sm text-slate-300" htmlFor="email">
                    Email
                  </label>
                  <input
                    id="email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="w-full rounded-2xl border border-white/10 bg-slate-950/70 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-400/50 focus:ring-2 focus:ring-cyan-400/20"
                  />
                </div>

                <div className="space-y-2">
                  <label className="block text-sm text-slate-300" htmlFor="password">
                    Password
                  </label>
                  <input
                    id="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full rounded-2xl border border-white/10 bg-slate-950/70 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-400/50 focus:ring-2 focus:ring-cyan-400/20"
                  />
                </div>

                <button
                  type="submit"
                  disabled={submitting}
                  className="w-full rounded-2xl bg-cyan-400 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {submitting
                    ? mode === "login"
                      ? "Signing in..."
                      : "Creating account..."
                    : mode === "login"
                      ? "Sign in"
                      : "Create account"}
                </button>
              </form>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
