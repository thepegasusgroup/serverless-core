"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import { AppShell } from "@/components/app-shell";

type RequestLog = {
  id: string;
  api_key_id: string | null;
  instance_id: string | null;
  model_slug: string | null;
  path: string | null;
  streaming: boolean | null;
  status_code: number | null;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error: string | null;
  created_at: string;
};

type ApiKey = { id: string; label: string; prefix: string };

const statusColor = (s: number | null) => {
  if (s === null) return "text-zinc-500";
  if (s >= 500) return "text-red-400";
  if (s >= 400) return "text-orange-400";
  if (s >= 200) return "text-green-400";
  return "text-zinc-400";
};

export default function LogsPage() {
  const [rows, setRows] = useState<RequestLog[]>([]);
  const [keys, setKeys] = useState<Record<string, ApiKey>>({});
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    const supabase = createClient();
    const [logsRes, keysRes] = await Promise.all([
      supabase
        .from("request_logs")
        .select("*")
        .order("created_at", { ascending: false })
        .limit(200),
      supabase.from("api_keys").select("id,label,prefix"),
    ]);
    if (logsRes.error) toast.error(logsRes.error.message);
    if (logsRes.data) setRows(logsRes.data as RequestLog[]);
    if (keysRes.data) {
      const m: Record<string, ApiKey> = {};
      for (const k of keysRes.data as ApiKey[]) m[k.id] = k;
      setKeys(m);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    const supabase = createClient();
    fetchAll();
    const ch = supabase
      .channel("request-logs-live")
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "request_logs" },
        () => fetchAll(),
      )
      .subscribe();
    return () => {
      supabase.removeChannel(ch);
    };
  }, [fetchAll]);

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <header className="mb-6">
          <h1 className="text-3xl font-semibold">Request logs</h1>
          <p className="text-sm text-zinc-500 mt-1">
            Last 200 calls to <code className="text-zinc-300">/v1/*</code>. Live.
          </p>
        </header>

        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3 text-left font-medium">time</th>
                <th className="px-4 py-3 text-left font-medium">key</th>
                <th className="px-4 py-3 text-left font-medium">model</th>
                <th className="px-4 py-3 text-left font-medium">path</th>
                <th className="px-4 py-3 text-right font-medium">status</th>
                <th className="px-4 py-3 text-right font-medium">latency</th>
                <th className="px-4 py-3 text-right font-medium">in</th>
                <th className="px-4 py-3 text-right font-medium">out</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={8} className="px-4 py-12 text-center text-zinc-500">
                    loading…
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-12 text-center text-zinc-500">
                    no requests yet — hit{" "}
                    <code className="text-zinc-300">/v1/chat/completions</code> with a
                    valid key
                  </td>
                </tr>
              ) : (
                rows.map((r) => {
                  const keyLabel = r.api_key_id
                    ? keys[r.api_key_id]?.label ?? r.api_key_id.slice(0, 8)
                    : "—";
                  return (
                    <tr
                      key={r.id}
                      className="border-t border-zinc-800 hover:bg-zinc-900/30"
                    >
                      <td className="px-4 py-3 text-xs text-zinc-400 tabular-nums whitespace-nowrap">
                        {new Date(r.created_at).toLocaleTimeString()}
                      </td>
                      <td className="px-4 py-3 text-xs">{keyLabel}</td>
                      <td className="px-4 py-3 text-xs font-mono text-zinc-300">
                        {r.model_slug ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-xs font-mono text-zinc-400">
                        {r.path ?? "—"}
                        {r.streaming && (
                          <span className="ml-1 text-[10px] text-blue-400">stream</span>
                        )}
                      </td>
                      <td
                        className={`px-4 py-3 text-xs text-right tabular-nums font-medium ${statusColor(r.status_code)}`}
                      >
                        {r.status_code ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-xs text-right tabular-nums text-zinc-400">
                        {r.latency_ms != null ? `${r.latency_ms}ms` : "—"}
                      </td>
                      <td className="px-4 py-3 text-xs text-right tabular-nums text-zinc-400">
                        {r.prompt_tokens ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-xs text-right tabular-nums text-zinc-400">
                        {r.completion_tokens ?? "—"}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </main>
    </AppShell>
  );
}
