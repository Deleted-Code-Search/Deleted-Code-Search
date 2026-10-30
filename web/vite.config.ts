import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API 는 localhost:8000 (docker compose up api). 주소는 VITE_API_BASE 로 바꾼다.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
