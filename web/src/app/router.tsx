import { createBrowserRouter } from "react-router-dom";

// Pages will be added in subsequent phases
const router = createBrowserRouter([
  {
    path: "*",
    element: <div className="p-8 text-white">RF Platform — loading…</div>,
  },
]);

export { router };
