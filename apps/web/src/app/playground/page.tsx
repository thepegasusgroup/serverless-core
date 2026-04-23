"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Play, Square } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { AppShell } from "@/components/app-shell";

type Model = { id: string; slug: string; hf_repo: string };

const KEY_STORAGE = "sc_playground_api_key";
const SYSTEM_STORAGE = "sc_playground_system";
const USER_STORAGE = "sc_playground_user";

export default function PlaygroundPage() {
  const [models, setModels] = useState<Model[]>([]);
  const [modelSlug, setModelSlug] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("You are a helpful assistant.");
  const [userPrompt, setUserPrompt] = useState("");
  const [streaming, setStreaming] = useState(true);
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(512);

  const [response, setResponse] = useState("");
  const [running, setRunning] = useState(false);
  const [metrics, setMetrics] = useState<{
    ttfbMs?: number;
    totalMs?: number;
    tokens?: number;
  }>({});
  const abortRef = useRef<AbortController | null>(null);

  // Load models + saved values from localStorage.
  useEffect(() => {
    const supabase = createClient();
    supabase
      .from("models")
      .select("id,slug,hf_repo")
      .eq("enabled", true)
      .then(({ data }) => {
        if (data) {
          setModels(data as Model[]);
          if (data.length && !modelSlug) setModelSlug(data[0].slug);
        }
      });
    const storedKey = localStorage.getItem(KEY_STORAGE);
    if (storedKey) setApiKey(storedKey);
    const storedSys = localStorage.getItem(SYSTEM_STORAGE);
    if (storedSys) setSystemPrompt(storedSys);
    const storedUser = localStorage.getItem(USER_STORAGE);
    if (storedUser) setUserPrompt(storedUser);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (apiKey) localStorage.setItem(KEY_STORAGE, apiKey);
  }, [apiKey]);
  useEffect(() => {
    localStorage.setItem(SYSTEM_STORAGE, systemPrompt);
  }, [systemPrompt]);
  useEffect(() => {
    localStorage.setItem(USER_STORAGE, userPrompt);
  }, [userPrompt]);

  const run = useCallback(async () => {
    if (!apiKey) {
      toast.error("Paste an API key first (create one at /keys)");
      return;
    }
    if (!userPrompt.trim()) {
      toast.error("Enter a prompt");
      return;
    }
    setResponse("");
    setMetrics({});
    setRunning(true);
    const ac = new AbortController();
    abortRef.current = ac;
    const t0 = performance.now();
    let ttfbMs: number | undefined;
    let tokenCount = 0;

    const messages = systemPrompt.trim()
      ? [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPrompt },
        ]
      : [{ role: "user", content: userPrompt }];

    const body = {
      model: modelSlug,
      messages,
      stream: streaming,
      temperature,
      max_tokens: maxTokens,
    };

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
    try {
      const res = await fetch(`${apiUrl}/v1/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify(body),
        signal: ac.signal,
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status}: ${text}`);
      }

      if (!streaming) {
        const data = await res.json();
        ttfbMs = Math.round(performance.now() - t0);
        const content = data.choices?.[0]?.message?.content ?? "";
        setResponse(content);
        setMetrics({
          ttfbMs,
          totalMs: ttfbMs,
          tokens: data.usage?.completion_tokens,
        });
      } else {
        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            const s = line.trim();
            if (!s || !s.startsWith("data:")) continue;
            const json = s.slice(5).trim();
            if (json === "[DONE]") continue;
            try {
              const chunk = JSON.parse(json);
              const delta = chunk.choices?.[0]?.delta?.content;
              if (delta) {
                if (ttfbMs === undefined) {
                  ttfbMs = Math.round(performance.now() - t0);
                  setMetrics((m) => ({ ...m, ttfbMs }));
                }
                tokenCount += 1;
                setResponse((p) => p + delta);
              }
            } catch {
              /* ignore */
            }
          }
        }
        setMetrics({
          ttfbMs,
          totalMs: Math.round(performance.now() - t0),
          tokens: tokenCount,
        });
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        toast.info("Aborted");
      } else {
        toast.error((e as Error).message);
        setResponse(`(error: ${(e as Error).message})`);
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }, [apiKey, userPrompt, systemPrompt, modelSlug, streaming, temperature, maxTokens]);

  const stop = () => abortRef.current?.abort();

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <header className="mb-6">
          <h1 className="text-3xl font-semibold">Playground</h1>
          <p className="text-sm text-zinc-500 mt-1">
            Test your API end-to-end without leaving the dashboard.
          </p>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* LEFT: controls */}
          <aside className="space-y-4 lg:col-span-1">
            <Field label="API key">
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sc_live_..."
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono focus:border-zinc-500 focus:outline-none"
              />
              <span className="mt-1 text-[10px] text-zinc-500">
                Saved in browser localStorage.
              </span>
            </Field>

            <Field label="Model">
              <select
                value={modelSlug}
                onChange={(e) => setModelSlug(e.target.value)}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:border-zinc-500 focus:outline-none"
              >
                {models.map((m) => (
                  <option key={m.id} value={m.slug}>
                    {m.slug}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Streaming">
              <label className="mt-1 flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={streaming}
                  onChange={(e) => setStreaming(e.target.checked)}
                  className="h-4 w-4 accent-zinc-200"
                />
                SSE (tokens live)
              </label>
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="Temperature">
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  max="2"
                  value={temperature}
                  onChange={(e) => setTemperature(parseFloat(e.target.value))}
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
                />
              </Field>
              <Field label="Max tokens">
                <input
                  type="number"
                  min="1"
                  max="8192"
                  value={maxTokens}
                  onChange={(e) => setMaxTokens(parseInt(e.target.value) || 512)}
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
                />
              </Field>
            </div>

            <Field label="System prompt">
              <textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={3}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm font-mono focus:border-zinc-500 focus:outline-none"
              />
            </Field>
          </aside>

          {/* RIGHT: prompt + output */}
          <section className="lg:col-span-2 space-y-4">
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs uppercase tracking-wider text-zinc-500">
                  User prompt
                </span>
                <div className="flex items-center gap-2">
                  {!running ? (
                    <button
                      onClick={run}
                      disabled={!apiKey || !userPrompt.trim()}
                      className="flex items-center gap-1.5 rounded bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
                    >
                      <Play className="h-3.5 w-3.5" /> Run
                    </button>
                  ) : (
                    <button
                      onClick={stop}
                      className="flex items-center gap-1.5 rounded border border-red-900 bg-red-950/40 px-3 py-1.5 text-sm font-medium text-red-300 hover:bg-red-950/70"
                    >
                      <Square className="h-3.5 w-3.5" /> Stop
                    </button>
                  )}
                </div>
              </div>
              <textarea
                value={userPrompt}
                onChange={(e) => setUserPrompt(e.target.value)}
                rows={5}
                placeholder="Write a haiku about serverless GPUs."
                className="w-full rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs uppercase tracking-wider text-zinc-500">
                  Response
                </span>
                <div className="flex items-center gap-4 text-xs text-zinc-500 tabular-nums">
                  {metrics.ttfbMs !== undefined && (
                    <span>TTFB {metrics.ttfbMs}ms</span>
                  )}
                  {metrics.totalMs !== undefined && (
                    <span>total {metrics.totalMs}ms</span>
                  )}
                  {metrics.tokens !== undefined && (
                    <span>{metrics.tokens} tokens</span>
                  )}
                </div>
              </div>
              <pre className="min-h-[300px] rounded-xl border border-zinc-800 bg-black p-4 text-sm font-mono text-zinc-200 whitespace-pre-wrap overflow-auto">
                {response || (running ? "…" : "(output will appear here)")}
              </pre>
            </div>
          </section>
        </div>
      </main>
    </AppShell>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {children}
    </label>
  );
}
