import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // dev proxy so /api hits the backend without CORS pain
    proxy: { "/api": "http://localhost:3001" },
  },
});
