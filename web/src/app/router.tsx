import { createBrowserRouter } from "react-router-dom";
import { ProtectedRoute } from "../components/ProtectedRoute";
import { LoginPage } from "../pages/LoginPage";
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
        <div className="p-8 text-white">Agents — coming in Phase 5</div>
      </ProtectedRoute>
    ),
  },
  {
    path: "/agents/:id/live",
    element: (
      <ProtectedRoute>
        <div className="p-8 text-white">Live viewer — coming in Phase 9</div>
      </ProtectedRoute>
    ),
  },
  {
    path: "/agents/:id/tokens",
    element: (
      <ProtectedRoute>
        <div className="p-8 text-white">Token management — coming in Phase 6</div>
      </ProtectedRoute>
    ),
  },
  {
    path: "*",
    element: <NotFoundPage />,
  },
]);

export { router };
