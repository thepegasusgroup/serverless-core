import { createClient } from "@/lib/supabase/client";

/**
 * Call the serverless-core FastAPI control plane with the current Supabase
 * session token attached. Throws on HTTP errors with the response body in
 * the message so UI code can surface it.
 */
export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const token = session?.access_token;

  const url =
    (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
      /\/$/,
      "",
    ) + path;

  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as T;
}
