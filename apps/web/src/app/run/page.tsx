"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Play, Square } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { AppShell } from "@/components/app-shell";
import { api } from "@/lib/api";

type Model = { id: string; slug: string; hf_repo: string };
type Pipeline = { id: string; slug: string; label: string; output_mode: string };

const SYSTEM_STORAGE = "sc_run_system";
const USER_STORAGE = "sc_run_user";

const DEFAULT_SYSTEM = "You are a helpful assistant. Answer concisely.";
const DEFAULT_USER = "Write a short haiku about serverless GPUs.";

type TargetMode = "model" | "pipeline";

export default function RunPage() {
  const [targetMode, setTargetMode] = useState<TargetMode>("model");
  const [models, setModels] = useState<Model[]>([]);
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [modelSlug, setModelSlug] = useState("");
  const [pipelineSlug, setPipelineSlug] = useState("");
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

  // Debug event log — mix of client events + Supabase Realtime instance changes.
  type Event = {
    t: number; // ms since run start
    kind: "info" | "net" | "warn" | "error" | "instance";
    text: string;
  };
  const [events, setEvents] = useState<Event[]>([]);
  const runStartRef = useRef<number>(0);
  const logEl = useRef<HTMLDivElement>(null);

  const logEvt = useCallback((kind: Event["kind"], text: string) => {
    const t = runStartRef.current
      ? Math.max(0, performance.now() - runStartRef.current)
      : 0;
    setEvents((prev) => [...prev, { t, kind, text }]);
    setTimeout(() => {
      const el = logEl.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, 0);
  }, []);

  // Subscribe to instance status changes for live backend visibility.
  useEffect(() => {
    const supabase = createClient();
    const ch = supabase
      .channel("run-instance-watch")
      .on(
        "postgres_changes",
        { event: "UPDATE", schema: "public", table: "instances" },
        (payload) => {
          const next = payload.new as {
            id: string;
            status: string;
            stage_msg?: string | null;
          };
          if (!runStartRef.current) return; // only log during a run
          const short = next.id.slice(0, 8);
          const msg = next.stage_msg
            ? `${short} → ${next.status} · ${next.stage_msg}`
            : `${short} → ${next.status}`;
          logEvt("instance", msg);
        },
      )
      .subscribe();
    return () => {
      supabase.removeChannel(ch);
    };
  }, [logEvt]);

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
    supabase
      .from("pipelines")
      .select("id,slug,label,output_mode")
      .eq("enabled", true)
      .then(({ data }) => {
        if (data) {
          setPipelines(data as Pipeline[]);
          if (data.length && !pipelineSlug) setPipelineSlug(data[0].slug);
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
    setEvents([]);
    setRunning(true);
    const ac = new AbortController();
    abortRef.current = ac;
    const t0 = performance.now();
    runStartRef.current = t0;
    let ttfbMs: number | undefined;
    let tokenCount = 0;
    logEvt(
      "info",
      targetMode === "model"
        ? `Run started · model=${modelSlug} · stream=${streaming}`
        : `Run started · pipeline=${pipelineSlug}`,
    );

    // Body shape differs by target mode:
    //   Model: we own system + stream + temp/max_tokens
    //   Pipeline: pipeline config decides those; client sends just messages
    const body: Record<string, unknown> =
      targetMode === "model"
        ? {
            model: modelSlug,
            messages: systemPrompt.trim()
              ? [
                  { role: "system", content: systemPrompt },
                  { role: "user", content: userPrompt },
                ]
              : [{ role: "user", content: userPrompt }],
            stream: streaming,
            temperature,
            max_tokens: maxTokens,
          }
        : {
            messages: [{ role: "user", content: userPrompt }],
            stream: streaming,
          };

    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
    const endpoint =
      targetMode === "model"
        ? `${apiUrl}/admin/playground/chat`
        : `${apiUrl}/admin/playground/pipeline/${pipelineSlug}`;

    try {
      logEvt("net", `POST ${endpoint}`);
      const res = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session?.access_token ?? ""}`,
        },
        body: JSON.stringify(body),
        signal: ac.signal,
      });
      logEvt("net", `Headers received · HTTP ${res.status}`);

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
        logEvt(
          "info",
          `Complete · ${ttfbMs}ms · ${data.usage?.completion_tokens ?? "?"} tokens`,
        );
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
                  logEvt("net", `First token · TTFB ${ttfbMs}ms`);
                }
                tokenCount += 1;
                setResponse((p) => p + delta);
              }
            } catch {
              /* ignore */
            }
          }
        }
        const totalMs = Math.round(performance.now() - t0);
        setMetrics({ ttfbMs, totalMs, tokens: tokenCount });
        logEvt("info", `Stream complete · ${totalMs}ms · ${tokenCount} chunks`);
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        toast.info("Aborted");
        logEvt("warn", "Request aborted by user");
      } else {
        toast.error((e as Error).message);
        setResponse(`(error: ${(e as Error).message})`);
        logEvt("error", (e as Error).message);
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
      runStartRef.current = 0;
    }
  }, [
    userPrompt,
    systemPrompt,
    modelSlug,
    pipelineSlug,
    targetMode,
    streaming,
    temperature,
    maxTokens,
    logEvt,
  ]);

  const stop = () => abortRef.current?.abort();

  return (
    <AppShell>
      <main className="h-screen flex flex-col p-6 pb-0 overflow-hidden">
        {/* top bar */}
        <header className="mb-4 flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-semibold">Run</h1>
            <p className="text-xs text-zinc-500 mt-0.5">
              Staff test console · auth via your Supabase session · target a
              raw model or a pipeline
            </p>
          </div>
          <div className="flex items-end gap-3 flex-wrap">
            {/* Mode toggle */}
            <LabeledInline label="target">
              <div className="inline-flex rounded border border-zinc-700 bg-zinc-900 overflow-hidden text-sm">
                <button
                  type="button"
                  onClick={() => setTargetMode("model")}
                  className={`px-3 py-1.5 ${
                    targetMode === "model"
                      ? "bg-zinc-100 text-zinc-900"
                      : "text-zinc-400 hover:text-zinc-100"
                  }`}
                >
                  model
                </button>
                <button
                  type="button"
                  onClick={() => setTargetMode("pipeline")}
                  className={`px-3 py-1.5 ${
                    targetMode === "pipeline"
                      ? "bg-zinc-100 text-zinc-900"
                      : "text-zinc-400 hover:text-zinc-100"
                  }`}
                >
                  pipeline
                </button>
              </div>
            </LabeledInline>

            {targetMode === "model" ? (
              <>
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
              </>
            ) : (
              <LabeledInline label="pipeline">
                <select
                  value={pipelineSlug}
                  onChange={(e) => setPipelineSlug(e.target.value)}
                  className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:border-zinc-500 focus:outline-none min-w-[220px]"
                >
                  {pipelines.length === 0 && (
                    <option value="">(no pipelines)</option>
                  )}
                  {pipelines.map((p) => (
                    <option key={p.id} value={p.slug}>
                      {p.slug} · {p.output_mode}
                    </option>
                  ))}
                </select>
              </LabeledInline>
            )}
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

          {/* RIGHT: response (top) + debug log (bottom) */}
          <div className="flex flex-col gap-3 min-h-0">
            {/* response */}
            <div className="flex flex-col flex-1 min-h-0">
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

            {/* debug log */}
            <div className="flex flex-col min-h-0 h-[30%]">
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                Pipeline log
              </div>
              <div
                ref={logEl}
                className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 p-2 text-[11px] font-mono overflow-auto"
              >
                {events.length === 0 ? (
                  <div className="text-zinc-600 p-2">
                    (client events + live instance state changes appear here during
                    a run)
                  </div>
                ) : (
                  events.map((e, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-2 px-1 py-0.5 border-b border-zinc-900 last:border-b-0"
                    >
                      <span className="text-zinc-600 tabular-nums w-14 shrink-0">
                        +{(e.t / 1000).toFixed(2)}s
                      </span>
                      <span
                        className={`uppercase text-[9px] tracking-wider w-16 shrink-0 ${
                          e.kind === "error"
                            ? "text-red-400"
                            : e.kind === "warn"
                              ? "text-yellow-400"
                              : e.kind === "instance"
                                ? "text-blue-400"
                                : e.kind === "net"
                                  ? "text-purple-400"
                                  : "text-zinc-400"
                        }`}
                      >
                        {e.kind}
                      </span>
                      <span className="text-zinc-200 break-all">{e.text}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
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
