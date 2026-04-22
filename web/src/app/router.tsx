import { createBrowserRouter, Navigate } from "react-router-dom";
import { ProtectedRoute } from "../components/ProtectedRoute";
import { LoginPage } from "../pages/LoginPage";
import { AgentsPage } from "../pages/AgentsPage";
import { AgentTokensPage } from "../pages/AgentTokensPage";
import { AgentLivePage } from "../pages/AgentLivePage";
import { AgentConnectGuidePage } from "../pages/AgentConnectGuidePage";
import { NotFoundPage } from "../pages/NotFoundPage";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to="/login" replace />,
  },
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/agents",
    element: (
      <ProtectedRoute>
        <AgentsPage />
      </ProtectedRoute>
    ),
  },
  {
    path: "/agents/:agentId/live",
    element: (
      <ProtectedRoute>
        <AgentLivePage />
      </ProtectedRoute>
    ),
  },
  {
    path: "/agents/:agentId/tokens",
    element: (
      <ProtectedRoute>
        <AgentTokensPage />
      </ProtectedRoute>
    ),
  },
  {
    path: "/agents/:agentId/connect",
    element: (
      <ProtectedRoute>
        <AgentConnectGuidePage />
      </ProtectedRoute>
    ),
  },
  {
    path: "*",
    element: <NotFoundPage />,
  },
]);

export { router };
