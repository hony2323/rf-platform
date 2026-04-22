import { Navigate } from "react-router-dom";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { UnauthorizedError } from "../api/client";

export function HomePage() {
  const { data, isLoading, error } = useCurrentUser();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950">
        <span className="text-sm text-slate-400">Loading...</span>
      </div>
    );
  }

  if (error instanceof UnauthorizedError) {
    return <Navigate to="/login" replace />;
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 p-4">
        <span className="text-sm text-slate-400">
          Failed to verify your session. Please try again.
        </span>
      </div>
    );
  }

  if (data) {
    return <Navigate to="/agents" replace />;
  }

  return <Navigate to="/login" replace />;
}
