import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { UnauthorizedError } from "../api/client";

interface Props {
  children: ReactNode;
}

export function ProtectedRoute({ children }: Props) {
  const { data, isLoading, error } = useCurrentUser();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <span className="text-gray-400 text-sm">Loading…</span>
      </div>
    );
  }

  if (error instanceof UnauthorizedError || !data) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
