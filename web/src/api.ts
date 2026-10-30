// API 는 docker compose 의 api 서비스 또는 로컬 uvicorn (localhost:8000). CORS 로 5173 에서 부른다.
export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

// 실패하면 API 의 detail(예: "python -m search.index 먼저") 을 그대로 오류 문구로 쓴다.
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { signal });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return (await res.json()) as T;
}
