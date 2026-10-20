import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Tauri expects a fixed dev port and must not clear its terminal output.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  server: { port: 1420, strictPort: true, watch: { ignored: ["**/src-tauri/**"] } },
  // Monaco is one lazy chunk (~2.7 MB) loaded from disk only when a diff opens.
  build: { target: "es2022", sourcemap: false, chunkSizeWarningLimit: 3000 },
  test: { environment: "jsdom", globals: false },
});
