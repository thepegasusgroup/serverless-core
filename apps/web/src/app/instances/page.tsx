"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
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

export default function InstancesPage() {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [loading, setLoading] = useState(true);
  const [destroying, setDestroying] = useState<string | null>(null);
  const [showDestroyed, setShowDestroyed] = useState(false);
  const [tick, setTick] = useState(0); // forces uptime re-render

  const fetchAll = useCallback(async () => {
    const supabase = createClient();
    const { data } = await supabase
      .from("instances")
      .select("*")
      .order("created_at", { ascending: false });
    if (data) setInstances(data as Instance[]);
    setLoading(false);
  }, []);

  useEffect(() => {
    const supabase = createClient();
    fetchAll();

    const channel = supabase
      .channel("instances-live")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "instances" },
        () => fetchAll(),
      )
      .subscribe();

    // re-render uptime every 5s
    const interval = setInterval(() => setTick((t) => t + 1), 5000);

    return () => {
      supabase.removeChannel(channel);
      clearInterval(interval);
    };
  }, [fetchAll]);

  const destroy = async (id: string) => {
    if (
      !confirm(
        `Destroy ${id.slice(0, 8)}? This stops vast.ai billing immediately.`,
      )
    )
      return;
    setDestroying(id);
    try {
      await api(`/admin/instances/${id}`, { method: "DELETE" });
      toast.success(`Destroyed ${id.slice(0, 8)}`);
      await fetchAll();
    } catch (e) {
      toast.error(`Destroy failed: ${(e as Error).message}`);
    } finally {
      setDestroying(null);
    }
  };

  const activeCount = instances.filter((i) => i.status !== "destroyed").length;
  const destroyedCount = instances.length - activeCount;
  const visible = showDestroyed
    ? instances
    : instances.filter((i) => i.status !== "destroyed");

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <header className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-3xl font-semibold">Instances</h1>
            <p className="text-sm text-zinc-500 mt-1">
              {activeCount} active · live via Supabase Realtime
            </p>
          </div>
          {destroyedCount > 0 && (
            <button
              onClick={() => setShowDestroyed((v) => !v)}
              className="text-xs text-zinc-500 hover:text-zinc-200 underline"
            >
              {showDestroyed
                ? `Hide destroyed (${destroyedCount})`
                : `Show destroyed (${destroyedCount})`}
            </button>
          )}
        </header>

        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3 text-left font-medium">id</th>
                <th className="px-4 py-3 text-left font-medium">status</th>
                <th className="px-4 py-3 text-left font-medium">vast</th>
                <th className="px-4 py-3 text-left font-medium">endpoint</th>
                <th className="px-4 py-3 text-left font-medium">uptime</th>
                <th className="px-4 py-3 text-left font-medium">heartbeat</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody data-tick={tick}>
              {loading ? (
                <tr>
                  <td
                    colSpan={7}
                    className="px-4 py-12 text-center text-zinc-500"
                  >
                    loading…
                  </td>
                </tr>
              ) : visible.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className="px-4 py-12 text-center text-zinc-500"
                  >
                    no instances yet — click{" "}
                    <a
                      href="/instances/new"
                      className="text-zinc-200 underline hover:text-white"
                    >
                      Rent
                    </a>{" "}
                    above
                  </td>
                </tr>
              ) : (
                visible.map((row) => {
                  const isActive = row.status !== "destroyed";
                  return (
                    <tr
                      key={row.id}
                      className="border-t border-zinc-800 hover:bg-zinc-900/30 transition-colors"
                    >
                      <td className="px-4 py-3 font-mono text-xs text-zinc-300">
                        {row.id.slice(0, 8)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-1">
                          <StatusBadge status={row.status} />
                          {row.stage_msg && row.status !== "destroyed" && (
                            <span className="text-[11px] text-zinc-500 max-w-xs truncate">
                              {row.stage_msg}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-zinc-400">
                        {row.vast_contract_id ?? "—"}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-zinc-400">
                        {row.ip ? `${row.ip}:${row.port}` : "—"}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-400 tabular-nums">
                        {uptime(row.registered_at ?? row.created_at)}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-400 tabular-nums">
                        {fmtTime(row.last_heartbeat_at)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {isActive && (
                          <button
                            onClick={() => destroy(row.id)}
                            disabled={destroying !== null}
                            className="rounded border border-red-900 bg-red-950/40 px-2.5 py-1 text-xs font-medium text-red-300 hover:bg-red-950/70 hover:text-red-200 disabled:opacity-60"
                          >
                            {destroying === row.id ? "…" : "destroy"}
                          </button>
                        )}
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
