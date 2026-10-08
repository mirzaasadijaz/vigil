const BASE = "/api";

export async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const isFormData = options.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (!isFormData) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    ...options,
    headers,
  });
  if (res.status === 401) {
    // Reload to trigger auth guard
    window.location.reload();
    throw new Error("Unauthorized");
  }
  return res;
}

export async function apiJson<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await apiFetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}
