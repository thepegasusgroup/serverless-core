"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { StatusBadge } from "@/components/status-badge";
import { uptime, fmtTime } from "@/lib/time";

type Instance = {
  id: string;
  vast_contract_id: number | null;
  status: string;
  ip: string | null;
  port: number | null;
  gpu_name: string | null;
  stage_msg: string | null;
  vast_actual_status: string | null;
  last_heartbeat_at: string | null;
  registered_at: string | null;
  created_at: string;
  destroyed_at: string | null;
};

const LOG_POLL_MS = 5000;

export default function InstanceDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [instance, setInstance] = useState<Instance | null>(null);
  const [logs, setLogs] = useState<string>("");
  const [logsLoading, setLogsLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [destroying, setDestroying] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);

  const fetchInstance = useCallback(async () => {
    const supabase = createClient();
    const { data } = await supabase
      .from("instances")
      .select("*")
      .eq("id", id)
      .limit(1);
    if (data && data[0]) setInstance(data[0] as Instance);
  }, [id]);

  const fetchLogs = useCallback(async () => {
    try {
      const res = await api<{ logs: string; note?: string }>(
        `/admin/instances/${id}/logs?tail=400`,
      );
      const text = res.logs || res.note || "";
      setLogs(text);
      // Auto-scroll to bottom if user hasn't scrolled up.
      setTimeout(() => {
        const el = logRef.current;
        if (el) el.scrollTop = el.scrollHeight;
      }, 0);
    } catch (e) {
      setLogs(`(failed to load logs: ${(e as Error).message})`);
    } finally {
      setLogsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchInstance();
    fetchLogs();

    const supabase = createClient();
    const channel = supabase
      .channel(`instance-${id}`)
      .on(
        "postgres_changes",
        { event: "UPDATE", schema: "public", table: "instances", filter: `id=eq.${id}` },
        () => fetchInstance(),
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [id, fetchInstance, fetchLogs]);

  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(fetchLogs, LOG_POLL_MS);
    return () => clearInterval(t);
  }, [autoRefresh, fetchLogs]);

  const destroy = async () => {
    if (!confirm("Destroy this instance? Stops vast.ai billing immediately."))
      return;
    setDestroying(true);
    try {
      await api(`/admin/instances/${id}`, { method: "DELETE" });
      toast.success("Destroyed");
      router.push("/instances");
    } catch (e) {
      toast.error(`Destroy failed: ${(e as Error).message}`);
      setDestroying(false);
    }
  };

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <Link
          href="/instances"
          className="inline-flex items-center gap-1.5 text-sm text-zinc-400 hover:text-zinc-200 mb-4"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          All instances
        </Link>

        <header className="mb-6 flex items-start justify-between gap-6">
          <div>
            <h1 className="text-2xl font-semibold font-mono">
              {id.slice(0, 8)}
              <span className="text-zinc-600">{id.slice(8)}</span>
            </h1>
            {instance && (
              <div className="mt-2 flex items-center gap-3">
                <StatusBadge status={instance.status} />
                {instance.stage_msg && instance.status !== "destroyed" && (
                  <span className="text-xs text-zinc-500">
                    {instance.stage_msg}
                  </span>
                )}
              </div>
            )}
          </div>
          {instance && instance.status !== "destroyed" && (
            <button
              onClick={destroy}
              disabled={destroying}
              className="rounded border border-red-900 bg-red-950/40 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-950/70 disabled:opacity-60"
            >
              {destroying ? "Destroying…" : "Destroy"}
            </button>
          )}
        </header>

        {instance && (
          <section className="mb-6 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <Kv label="vast contract" value={instance.vast_contract_id ?? "—"} mono />
            <Kv
              label="endpoint"
              value={instance.ip ? `${instance.ip}:${instance.port}` : "—"}
              mono
            />
            <Kv
              label="uptime"
              value={uptime(instance.registered_at ?? instance.created_at)}
            />
            <Kv label="heartbeat" value={fmtTime(instance.last_heartbeat_at)} />
          </section>
        )}

        <section>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-medium text-zinc-300">Container logs</h2>
            <div className="flex items-center gap-3 text-xs text-zinc-500">
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                  className="h-3.5 w-3.5 accent-zinc-200"
                />
                auto-refresh ({LOG_POLL_MS / 1000}s)
              </label>
              <button
                onClick={fetchLogs}
                className="flex items-center gap-1 hover:text-zinc-200"
              >
                <RefreshCw className="h-3 w-3" />
                refresh
              </button>
            </div>
          </div>
          <pre
            ref={logRef}
            className="rounded-xl border border-zinc-800 bg-black p-4 text-[11px] font-mono text-zinc-300 overflow-auto max-h-[60vh] whitespace-pre-wrap leading-relaxed"
          >
            {logsLoading ? "loading…" : logs || "(no logs yet)"}
          </pre>
        </section>
      </main>
    </AppShell>
  );
}

function Kv({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-3">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      <div
        className={`mt-1 text-sm text-zinc-100 ${mono ? "font-mono text-xs" : ""}`}
      >
        {value}
      </div>
    </div>
  );
}
