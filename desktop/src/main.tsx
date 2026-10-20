import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { applyAppearance, loadAppearance } from "./lib/theme";
import "./styles.css";

applyAppearance(loadAppearance()); // before the first paint, so a forced theme or accent never flashes
// "System" settings follow the OS live.
for (const query of ["(prefers-color-scheme: light)", "(prefers-reduced-motion: reduce)"]) {
  window.matchMedia?.(query).addEventListener?.("change", () => applyAppearance(loadAppearance()));
}

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");
createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
