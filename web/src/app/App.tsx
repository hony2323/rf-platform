import { RouterProvider } from "react-router-dom";
import { SpeedInsights } from "@vercel/speed-insights/react";
import { router } from "./router";

export function App() {
  return (
    <>
      <RouterProvider router={router} />
      <SpeedInsights />
    </>
  );
}
