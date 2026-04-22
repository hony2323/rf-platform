import { createBrowserRouter } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { ProtectedRoute } from "../components/ProtectedRoute";
import { HomePage } from "../pages/HomePage";
import { LoginPage } from "../pages/LoginPage";
import { AgentsPage } from "../pages/AgentsPage";
import { AgentTokensPage } from "../pages/AgentTokensPage";
import { AgentLivePage } from "../pages/AgentLivePage";
import { AgentConnectGuidePage } from "../pages/AgentConnectGuidePage";
import { NotFoundPage } from "../pages/NotFoundPage";

const router = createBrowserRouter([
  {
    path: "/",
    element: <HomePage />,
  },
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/agents",
    element: (
      <ProtectedRoute>
        <AppShell
          title="Control Center"
          subtitle="Manage agents, open live views, and keep connection details close at hand."
        >
          <AgentsPage />
        </AppShell>
      </ProtectedRoute>
    ),
  },
  {
    path: "/agents/:agentId/live",
    element: (
      <ProtectedRoute>
        <AppShell
          title="Live Spectrum"
          subtitle="Monitor the stream, track session state, and jump back home any time."
        >
          <AgentLivePage />
        </AppShell>
      </ProtectedRoute>
    ),
  },
  {
    path: "/agents/:agentId/tokens",
    element: (
      <ProtectedRoute>
        <AppShell
          title="Agent Tokens"
          subtitle="Issue and revoke access tokens for a specific agent."
        >
          <AgentTokensPage />
        </AppShell>
      </ProtectedRoute>
    ),
  },
  {
    path: "/agents/:agentId/connect",
    element: (
      <ProtectedRoute>
        <AppShell
          title="Connect Guide"
          subtitle="Create a token, launch the agent, and verify the live feed."
        >
          <AgentConnectGuidePage />
        </AppShell>
      </ProtectedRoute>
    ),
  },
  {
    path: "*",
    element: <NotFoundPage />,
  },
]);

export { router };
