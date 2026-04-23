"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Play, Square } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { AppShell } from "@/components/app-shell";
import { api } from "@/lib/api";

type Model = { id: string; slug: string; hf_repo: string };

const SYSTEM_STORAGE = "sc_playground_system";
const USER_STORAGE = "sc_playground_user";

const DEFAULT_SYSTEM = "You are a helpful assistant. Answer concisely.";
const DEFAULT_USER = "Write a short haiku about serverless GPUs.";

export default function PlaygroundPage() {
  const [models, setModels] = useState<Model[]>([]);
  const [modelSlug, setModelSlug] = useState("");
  const [systemPrompt, setSystemPrompt] = useState(DEFAULT_SYSTEM);
  const [userPrompt, setUserPrompt] = useState(DEFAULT_USER);
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
    const storedSys = localStorage.getItem(SYSTEM_STORAGE);
    if (storedSys) setSystemPrompt(storedSys);
    const storedUser = localStorage.getItem(USER_STORAGE);
    if (storedUser) setUserPrompt(storedUser);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    localStorage.setItem(SYSTEM_STORAGE, systemPrompt);
  }, [systemPrompt]);
  useEffect(() => {
    localStorage.setItem(USER_STORAGE, userPrompt);
  }, [userPrompt]);

  const run = useCallback(async () => {
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

    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";

    try {
      const res = await fetch(`${apiUrl}/admin/playground/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session?.access_token ?? ""}`,
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
  }, [userPrompt, systemPrompt, modelSlug, streaming, temperature, maxTokens]);

  const stop = () => abortRef.current?.abort();

  return (
    <AppShell>
      <main className="h-screen flex flex-col p-6 pb-0 overflow-hidden">
        {/* top bar */}
        <header className="mb-4 flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-semibold">Playground</h1>
            <p className="text-xs text-zinc-500 mt-0.5">
              Logged-in staff only · no API key needed · calls hit{" "}
              <code className="text-zinc-300">/admin/playground/chat</code>
            </p>
          </div>
          <div className="flex items-end gap-3">
            <LabeledInline label="model">
              <select
                value={modelSlug}
                onChange={(e) => setModelSlug(e.target.value)}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:border-zinc-500 focus:outline-none min-w-[180px]"
              >
                {models.map((m) => (
                  <option key={m.id} value={m.slug}>
                    {m.slug}
                  </option>
                ))}
              </select>
            </LabeledInline>
            <LabeledInline label="temp">
              <input
                type="number"
                step="0.1"
                min="0"
                max="2"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
                className="w-20 rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
              />
            </LabeledInline>
            <LabeledInline label="max tokens">
              <input
                type="number"
                min="1"
                max="8192"
                value={maxTokens}
                onChange={(e) => setMaxTokens(parseInt(e.target.value) || 512)}
                className="w-24 rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
              />
            </LabeledInline>
            <label className="flex items-center gap-2 text-sm text-zinc-300 cursor-pointer select-none px-2 py-1.5">
              <input
                type="checkbox"
                checked={streaming}
                onChange={(e) => setStreaming(e.target.checked)}
                className="h-4 w-4 accent-zinc-200"
              />
              stream
            </label>
            {!running ? (
              <button
                onClick={run}
                disabled={!userPrompt.trim()}
                className="flex items-center gap-1.5 rounded bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
              >
                <Play className="h-3.5 w-3.5" /> Run
              </button>
            ) : (
              <button
                onClick={stop}
                className="flex items-center gap-1.5 rounded border border-red-900 bg-red-950/40 px-4 py-2 text-sm font-medium text-red-300 hover:bg-red-950/70"
              >
                <Square className="h-3.5 w-3.5" /> Stop
              </button>
            )}
          </div>
        </header>

        {/* two-column body */}
        <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-4 min-h-0 pb-6">
          {/* LEFT: prompts */}
          <div className="flex flex-col gap-3 min-h-0">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                System prompt
              </div>
              <textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={3}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none resize-none"
              />
            </div>
            <div className="flex flex-col flex-1 min-h-0">
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                User prompt
              </div>
              <textarea
                value={userPrompt}
                onChange={(e) => setUserPrompt(e.target.value)}
                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none resize-none"
              />
            </div>
          </div>

          {/* RIGHT: response */}
          <div className="flex flex-col min-h-0">
            <div className="flex items-center justify-between mb-1">
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">
                Response
              </div>
              <div className="flex items-center gap-4 text-[10px] text-zinc-500 tabular-nums">
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
            <pre className="flex-1 rounded-lg border border-zinc-800 bg-black p-4 text-sm font-mono text-zinc-200 whitespace-pre-wrap overflow-auto">
              {response || (running ? "…" : "(output will appear here)")}
            </pre>
          </div>
        </div>
      </main>
    </AppShell>
  );
}

function LabeledInline({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-zinc-500 mb-0.5">
        {label}
      </span>
      {children}
    </div>
  );
}
