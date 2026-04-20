import { createBrowserRouter } from "react-router-dom";
import { ProtectedRoute } from "../components/ProtectedRoute";
import { LoginPage } from "../pages/LoginPage";
import { AgentsPage } from "../pages/AgentsPage";
import { AgentTokensPage } from "../pages/AgentTokensPage";
import { NotFoundPage } from "../pages/NotFoundPage";

const router = createBrowserRouter([
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
        <div className="p-8 text-white">Live viewer — coming in Phase 9</div>
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
    path: "*",
    element: <NotFoundPage />,
  },
]);

export { router };
