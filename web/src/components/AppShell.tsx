import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { logout } from "../api/auth";
import { useCurrentUser } from "../hooks/useCurrentUser";

interface AppShellProps {
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}

export function AppShell({
  title,
  subtitle,
  actions,
  children,
}: AppShellProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: user } = useCurrentUser();
  const [logoutError, setLogoutError] = useState<string | null>(null);

  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: async () => {
      setLogoutError(null);
      queryClient.setQueryData(["me"], null);
      await queryClient.invalidateQueries({ queryKey: ["me"] });
      navigate("/login", { replace: true });
    },
    onError: (error) => {
      setLogoutError(error instanceof Error ? error.message : "Logout failed.");
    },
  });

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-white/10 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-center gap-4">
              <Link
                to="/agents"
                className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-cyan-400/30 bg-cyan-400/10 text-lg font-semibold text-cyan-200 transition hover:border-cyan-300/50 hover:bg-cyan-400/20 hover:text-white"
                aria-label="Go to home dashboard"
              >
                RF
              </Link>
              <div>
                <p className="text-xs uppercase tracking-[0.28em] text-cyan-300/80">
                  RF Platform
                </p>
                <h1 className="text-lg font-semibold text-white">
                  {title ?? "Control Center"}
                </h1>
                {subtitle && (
                  <p className="text-sm text-slate-400">{subtitle}</p>
                )}
              </div>
            </div>

            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <nav className="flex items-center gap-2">
                <NavLink
                  to="/agents"
                  className={({ isActive }) =>
                    `rounded-full px-3 py-2 text-sm transition ${
                      isActive
                        ? "bg-white text-slate-950"
                        : "text-slate-300 hover:bg-white/10 hover:text-white"
                    }`
                  }
                >
                  Home
                </NavLink>
              </nav>

              <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/5 px-3 py-2">
                <div className="min-w-0">
                  <p className="text-xs uppercase tracking-[0.2em] text-slate-500">
                    Signed in
                  </p>
                  <p className="truncate text-sm text-slate-200">
                    {user?.email ?? "Unknown user"}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => logoutMutation.mutate()}
                  disabled={logoutMutation.isPending}
                  className="rounded-full border border-rose-400/30 px-3 py-2 text-sm text-rose-200 transition hover:bg-rose-400/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {logoutMutation.isPending ? "Logging out..." : "Log out"}
                </button>
              </div>
            </div>
          </div>

          {(actions || logoutError) && (
            <div className="flex flex-col gap-3 border-t border-white/10 pt-4 lg:flex-row lg:items-center lg:justify-between">
              <div>{actions}</div>
              {logoutError && (
                <p className="text-sm text-rose-300">{logoutError}</p>
              )}
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {children}
      </main>
    </div>
  );
}
