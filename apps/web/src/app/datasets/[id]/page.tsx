"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowLeft, Download, Copy } from "lucide-react";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { createClient } from "@/lib/supabase/client";
import { fmtTime } from "@/lib/time";

type Dataset = {
  id: string;
  slug: string;
  label: string;
  status: string;
  provider: string;
  config: {
    model: string;
    system: string;
    prompts: string[];
    max_tokens: number;
    cache_system: boolean;
  };
  progress_completed: number;
  progress_total: number;
  progress_errored: number;
  usage: {
    input_tokens?: number;
    output_tokens?: number;
    cache_read_input_tokens?: number;
    cache_creation_input_tokens?: number;
    cost_usd?: number;
  } | null;
  error: string | null;
  external_batch_id: string | null;
  submitted_at: string | null;
  completed_at: string | null;
  created_at: string;
};

type Row = {
  id: string;
  row_index: number;
  input: { system: string; user: string };
  output: string | null;
  usage: {
    input_tokens?: number;
    output_tokens?: number;
    cache_read_input_tokens?: number;
    cost_usd?: number;
  } | null;
  error: string | null;
};

const PAGE_SIZE = 25;

export default function DatasetDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id as string;
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Row | null>(null);

  const fetchDataset = useCallback(async () => {
    try {
      const d = await api<Dataset>(`/admin/datasets/${id}`);
      setDataset(d);
    } catch (e) {
      toast.error(`Load failed: ${(e as Error).message}`);
    }
  }, [id]);

  const fetchRows = useCallback(async () => {
    try {
      const r = await api<{ rows: Row[]; total: number }>(
        `/admin/datasets/${id}/rows?limit=${PAGE_SIZE}&offset=${offset}`,
      );
      setRows(r.rows);
      setTotal(r.total);
    } catch (e) {
      toast.error(`Rows load failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [id, offset]);

  useEffect(() => {
    fetchDataset();
    fetchRows();
    const supabase = createClient();
    const channel = supabase
      .channel(`dataset-${id}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "datasets",
          filter: `id=eq.${id}`,
        },
        () => {
          fetchDataset();
          fetchRows();
        },
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, [id, fetchDataset, fetchRows]);

  const download = async (fmt: "jsonl" | "csv") => {
    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
    const url = `${apiUrl}/admin/datasets/${id}/export?fmt=${fmt}`;
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${session?.access_token ?? ""}` },
    });
    if (!res.ok) {
      toast.error(`Export failed: ${res.status}`);
      return;
    }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${dataset?.slug ?? id}.${fmt}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (!dataset && loading)
    return (
      <AppShell>
        <main className="p-8 text-zinc-500">loading…</main>
      </AppShell>
    );
  if (!dataset)
    return (
      <AppShell>
        <main className="p-8 text-red-400">Dataset not found</main>
      </AppShell>
    );

  const cost = dataset.usage?.cost_usd ?? 0;
  const cacheRead = dataset.usage?.cache_read_input_tokens ?? 0;
  const cacheWrite = dataset.usage?.cache_creation_input_tokens ?? 0;
  const inTok = dataset.usage?.input_tokens ?? 0;
  const outTok = dataset.usage?.output_tokens ?? 0;

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <Link
          href="/datasets"
          className="inline-flex items-center gap-1 text-sm text-zinc-400 hover:text-zinc-200 mb-4"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> back to datasets
        </Link>

        <header className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-2xl font-semibold">{dataset.label}</h1>
            <div className="flex items-center gap-3 mt-1 text-xs text-zinc-500 font-mono">
              <span>{dataset.slug}</span>
              <span>·</span>
              <span>{dataset.config.model}</span>
              <span>·</span>
              <span>{dataset.status}</span>
            </div>
          </div>
          {dataset.status === "completed" && (
            <div className="flex items-center gap-1">
              <button
                onClick={() => download("jsonl")}
                className="flex items-center gap-1.5 rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-800"
              >
                <Download className="h-3.5 w-3.5" /> JSONL
              </button>
              <button
                onClick={() => download("csv")}
                className="flex items-center gap-1.5 rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-800"
              >
                <Download className="h-3.5 w-3.5" /> CSV
              </button>
            </div>
          )}
        </header>

        {/* Stats */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          <Stat
            label="progress"
            value={`${dataset.progress_completed + dataset.progress_errored}/${dataset.progress_total}`}
            sub={
              dataset.progress_errored > 0
                ? `${dataset.progress_errored} errored`
                : undefined
            }
          />
          <Stat
            label="cost"
            value={cost > 0 ? `$${cost.toFixed(4)}` : "—"}
            sub="batch (50% off)"
          />
          <Stat
            label="tokens"
            value={`${(inTok / 1000).toFixed(1)}K / ${(outTok / 1000).toFixed(1)}K`}
            sub="input / output"
          />
          <Stat
            label="cache"
            value={`${(cacheRead / 1000).toFixed(1)}K read`}
            sub={cacheWrite > 0 ? `${(cacheWrite / 1000).toFixed(1)}K write` : undefined}
          />
          <Stat
            label="submitted"
            value={fmtTime(dataset.submitted_at) || "—"}
            sub={dataset.completed_at ? fmtTime(dataset.completed_at) : undefined}
          />
        </div>

        {dataset.error && (
          <div className="mb-4 rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">
            <strong>Error:</strong> {dataset.error}
          </div>
        )}

        {/* Row table */}
        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3 text-left font-medium w-12">#</th>
                <th className="px-4 py-3 text-left font-medium">user prompt</th>
                <th className="px-4 py-3 text-left font-medium">response</th>
                <th className="px-4 py-3 text-right font-medium">tokens</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={4}
                    className="px-4 py-12 text-center text-zinc-500"
                  >
                    {dataset.status === "running"
                      ? "waiting for batch to complete…"
                      : "no rows yet"}
                  </td>
                </tr>
              ) : (
                rows.map((r) => (
                  <tr
                    key={r.id}
                    onClick={() => setSelected(r)}
                    className="border-t border-zinc-800 hover:bg-zinc-900/30 cursor-pointer"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-zinc-500">
                      {r.row_index}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-300 max-w-xs truncate">
                      {r.input.user}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-400 max-w-md truncate">
                      {r.error ? (
                        <span className="text-red-400">{r.error}</span>
                      ) : (
                        (r.output ?? "").slice(0, 160)
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-xs tabular-nums text-zinc-500">
                      {r.usage?.output_tokens ?? "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {total > PAGE_SIZE && (
          <div className="mt-3 flex items-center justify-between text-xs text-zinc-500">
            <span>
              Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of{" "}
              {total}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
              >
                prev
              </button>
              <button
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={offset + PAGE_SIZE >= total}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
              >
                next
              </button>
            </div>
          </div>
        )}

        {selected && <RowDialog row={selected} onClose={() => setSelected(null)} />}
      </main>
    </AppShell>
  );
}

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-3">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      <div className="text-lg font-semibold text-zinc-100 mt-1 tabular-nums">
        {value}
      </div>
      {sub && (
        <div className="text-[10px] text-zinc-500 mt-0.5 truncate">{sub}</div>
      )}
    </div>
  );
}

function RowDialog({ row, onClose }: { row: Row; onClose: () => void }) {
  const copy = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("Copied");
  };

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[90vh] overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-950 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Row {row.row_index}</h2>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-200 text-sm"
          >
            Close
          </button>
        </div>

        <div className="space-y-4">
          <Section label="User prompt" copyText={row.input.user} onCopy={copy}>
            <pre className="whitespace-pre-wrap text-xs font-mono text-zinc-300">
              {row.input.user}
            </pre>
          </Section>

          {row.error ? (
            <Section label="Error" copyText={row.error} onCopy={copy}>
              <pre className="whitespace-pre-wrap text-xs font-mono text-red-400">
                {row.error}
              </pre>
            </Section>
          ) : (
            <Section label="Response" copyText={row.output ?? ""} onCopy={copy}>
              <pre className="whitespace-pre-wrap text-xs font-mono text-zinc-200">
                {row.output}
              </pre>
            </Section>
          )}

          {row.usage && (
            <div className="rounded bg-zinc-900/50 p-3 text-xs text-zinc-400 grid grid-cols-2 gap-2">
              <div>input: {row.usage.input_tokens ?? 0}</div>
              <div>output: {row.usage.output_tokens ?? 0}</div>
              <div>
                cache read: {row.usage.cache_read_input_tokens ?? 0}
              </div>
              <div>
                cost:{" "}
                <span className="tabular-nums">
                  ${(row.usage.cost_usd ?? 0).toFixed(6)}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({
  label,
  children,
  copyText,
  onCopy,
}: {
  label: string;
  children: React.ReactNode;
  copyText: string;
  onCopy: (text: string) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="text-[10px] uppercase tracking-wider text-zinc-500">
          {label}
        </div>
        <button
          onClick={() => onCopy(copyText)}
          className="text-zinc-500 hover:text-zinc-200 flex items-center gap-1 text-[10px]"
        >
          <Copy className="h-3 w-3" /> copy
        </button>
      </div>
      <div className="rounded border border-zinc-800 bg-zinc-950 p-3 max-h-96 overflow-y-auto">
        {children}
      </div>
    </div>
  );
}
