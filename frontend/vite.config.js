import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port 5173 : déjà autorisé par défaut dans CORS_ALLOWED_ORIGINS et
// WEBSOCKET_ALLOWED_ORIGINS côté Django.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
