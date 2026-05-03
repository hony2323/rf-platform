import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { login, loginWithGoogle, signup } from "../api/auth";
import { ApiError, UnauthorizedError } from "../api/client";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? "";

interface PasswordStrength {
  minLength: boolean;
  hasUppercase: boolean;
  hasLowercase: boolean;
  hasDigit: boolean;
  hasSymbol: boolean;
}

function StrengthItem({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className={`flex items-center gap-1.5 text-xs ${ok ? "text-emerald-400" : "text-slate-500"}`}>
      <span className="text-[10px]">{ok ? "✓" : "○"}</span>
      {label}
    </li>
  );
}

export function LoginPage() {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const googleButtonRef = useRef<HTMLDivElement>(null);

  const strength = useMemo<PasswordStrength>(
    () => ({
      minLength: password.length >= 10,
      hasUppercase: /[A-Z]/.test(password),
      hasLowercase: /[a-z]/.test(password),
      hasDigit: /\d/.test(password),
      hasSymbol: /[^a-zA-Z0-9]/.test(password),
    }),
    [password],
  );

  // Dynamically load the GSI script and initialize the button.
  // Only runs when VITE_GOOGLE_CLIENT_ID is set — no third-party script
  // is loaded for deployments that don't use Google login.
  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;

    const init = () => {
      if (!window.google || !googleButtonRef.current) return;
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        use_fedcm_for_prompt: true,
        callback: async (response) => {
          setError(null);
          setSubmitting(true);
          try {
            const user = await loginWithGoogle(response.credential);
            queryClient.setQueryData(["me"], user);
            void navigate("/agents");
          } catch (err) {
            console.error("[google-auth]", err);
            if (err instanceof ApiError) {
              setError(err.message || "Google sign-in failed.");
            } else {
              setError("Google sign-in failed. Try again.");
            }
            setSubmitting(false);
          }
        },
      });
      window.google.accounts.id.renderButton(googleButtonRef.current, {
        theme: "filled_black",
        size: "large",
        width: googleButtonRef.current.offsetWidth || 400,
        text: "continue_with",
        shape: "rectangular",
        locale: "en",
      });
    };

    if (window.google) {
      init();
      return;
    }

    const GSI_SRC = "https://accounts.google.com/gsi/client";
    let script = document.querySelector(
      `script[src="${GSI_SRC}"]`,
    ) as HTMLScriptElement | null;
    if (!script) {
      script = document.createElement("script");
      script.src = GSI_SRC;
      script.async = true;
      script.defer = true;
      document.head.appendChild(script);
    }
    script.addEventListener("load", init);
    return () => script?.removeEventListener("load", init);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (mode === "signup" && password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

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

  function switchMode(next: "login" | "signup") {
    setMode(next);
    setError(null);
    setPassword("");
    setConfirmPassword("");
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(34,211,238,0.18),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.16),_transparent_30%),linear-gradient(180deg,_#020617_0%,_#0f172a_55%,_#111827_100%)] p-4">
      <div className="mx-auto flex min-h-screen max-w-6xl items-center justify-center">
        <div className="grid w-full overflow-hidden rounded-[2rem] border border-white/10 bg-slate-950/80 shadow-2xl shadow-cyan-950/30 backdrop-blur lg:grid-cols-[1.05fr_0.95fr]">
          <section className="hidden border-r border-white/10 bg-white/5 p-10 lg:flex lg:items-end">
            <div className="space-y-6">
              <p className="text-xs uppercase tracking-[0.3em] text-cyan-300">RF Platform</p>
              <div className="space-y-4">
                <h1 className="max-w-lg text-4xl font-semibold leading-tight text-white">
                  Watch live spectrum without digging through a cluttered UI.
                </h1>
                <p className="max-w-md text-base text-slate-300">
                  Sign in to open your agent dashboard, create tokens, and jump straight into the
                  live waterfall.
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
                  onClick={() => switchMode("login")}
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
                  onClick={() => switchMode("signup")}
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
                onSubmit={(e) => {
                  void handleSubmit(e);
                }}
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
                    autoComplete={mode === "login" ? "current-password" : "new-password"}
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full rounded-2xl border border-white/10 bg-slate-950/70 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-400/50 focus:ring-2 focus:ring-cyan-400/20"
                  />
                  {mode === "signup" && (
                    <ul className="mt-2 space-y-1 pl-1">
                      <StrengthItem ok={strength.minLength} label="At least 10 characters" />
                      <StrengthItem ok={strength.hasUppercase} label="At least one uppercase letter" />
                      <StrengthItem ok={strength.hasLowercase} label="At least one lowercase letter" />
                      <StrengthItem ok={strength.hasDigit} label="At least one digit" />
                      <StrengthItem ok={strength.hasSymbol} label="At least one symbol (!@# etc.)" />
                    </ul>
                  )}
                </div>

                {mode === "signup" && (
                  <div className="space-y-2">
                    <label className="block text-sm text-slate-300" htmlFor="confirm-password">
                      Confirm password
                    </label>
                    <input
                      id="confirm-password"
                      type="password"
                      autoComplete="new-password"
                      required
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      className={`w-full rounded-2xl border bg-slate-950/70 px-4 py-3 text-sm text-white outline-none transition focus:ring-2 focus:ring-cyan-400/20 ${
                        confirmPassword && confirmPassword !== password
                          ? "border-rose-400/50 focus:border-rose-400/50"
                          : "border-white/10 focus:border-cyan-400/50"
                      }`}
                    />
                    {confirmPassword && confirmPassword !== password && (
                      <p className="text-xs text-rose-400">Passwords do not match.</p>
                    )}
                  </div>
                )}

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

              {GOOGLE_CLIENT_ID && (
                <div className="space-y-4">
                  <div className="flex items-center gap-3">
                    <div className="h-px flex-1 bg-white/10" />
                    <span className="text-xs text-slate-500">or</span>
                    <div className="h-px flex-1 bg-white/10" />
                  </div>
                  <div ref={googleButtonRef} className="flex justify-center" />
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
