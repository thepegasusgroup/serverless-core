"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";
import { ArrowLeft, Download, Copy, Plus, Trash2, Check, X } from "lucide-react";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { createClient } from "@/lib/supabase/client";
import { fmtTime } from "@/lib/time";

type Dataset = {
  id: string;
  slug: string;
  label: string;
  kind: "synthesis" | "eval";
  status: string;
  provider: string;
  config: {
    model?: string;
    system?: string;
    prompts?: string[];
    max_tokens?: number;
    cache_system?: boolean;
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

// Per-row eval tracking. Free-form jsonb server-side; these are the fields
// the UI knows about. Anything extra round-trips untouched.
type RowMeta = {
  plugin_name?: string;
  complexity?: "tiny" | "small" | "medium" | "large" | "xl";
  compile?: boolean;
  runtime?: boolean;
  failure_type?: string;
  fix_applied?: string;
  notes?: string;
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
  meta: RowMeta;
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
  const [adding, setAdding] = useState(false);
  // Eval filters — undefined = any, true/false = filter on that value
  const [fCompile, setFCompile] = useState<boolean | undefined>(undefined);
  const [fRuntime, setFRuntime] = useState<boolean | undefined>(undefined);

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
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "dataset_rows",
          filter: `dataset_id=eq.${id}`,
        },
        () => {
          fetchRows();
        },
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, [id, fetchDataset, fetchRows]);

  const download = async (
    fmt: "jsonl" | "csv",
    filters?: { compile?: boolean; runtime?: boolean },
  ) => {
    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
    const params = new URLSearchParams({ fmt });
    if (filters?.compile !== undefined)
      params.set("compile", String(filters.compile));
    if (filters?.runtime !== undefined)
      params.set("runtime", String(filters.runtime));
    const url = `${apiUrl}/admin/datasets/${id}/export?${params.toString()}`;
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
    const suffix =
      filters?.compile && filters?.runtime ? "-trainable" : "-all";
    a.download = `${dataset?.slug ?? id}${suffix}.${fmt}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // Client-side filter (server-side filter is only applied in the export
  // endpoint). Keeps the live table view responsive to filter chip clicks
  // without re-fetching.
  const visibleRows = useMemo(() => {
    return rows.filter((r) => {
      if (fCompile !== undefined && !!r.meta?.compile !== fCompile)
        return false;
      if (fRuntime !== undefined && !!r.meta?.runtime !== fRuntime)
        return false;
      return true;
    });
  }, [rows, fCompile, fRuntime]);

  const deleteRow = async (row: Row) => {
    if (!confirm(`Delete row ${row.row_index}? This can't be undone.`)) return;
    try {
      await api(`/admin/datasets/${id}/rows/${row.id}`, { method: "DELETE" });
      toast.success("Deleted");
      fetchRows();
      fetchDataset();
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
    }
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
  const isEval = dataset.kind === "eval";

  // Eval-mode counters — source from current rows (cheap) so they update
  // live as you flip meta flags.
  const nCompile = rows.filter((r) => r.meta?.compile).length;
  const nRuntime = rows.filter((r) => r.meta?.runtime).length;
  const nTrainable = rows.filter(
    (r) => r.meta?.compile && r.meta?.runtime,
  ).length;

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
              <span>{dataset.kind}</span>
              {dataset.config.model && (
                <>
                  <span>·</span>
                  <span>{dataset.config.model}</span>
                </>
              )}
              <span>·</span>
              <span>{dataset.status}</span>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {isEval && (
              <button
                onClick={() => setAdding(true)}
                className="flex items-center gap-1.5 rounded bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200"
              >
                <Plus className="h-3.5 w-3.5" /> Add row
              </button>
            )}
            {(dataset.status === "completed" || isEval) && (
              <>
                {isEval && (
                  <button
                    onClick={() =>
                      download("jsonl", { compile: true, runtime: true })
                    }
                    className="flex items-center gap-1.5 rounded border border-green-900 bg-green-950/40 px-3 py-1.5 text-sm text-green-300 hover:bg-green-950/70"
                    title="Only rows where compile AND runtime passed"
                  >
                    <Download className="h-3.5 w-3.5" /> Trainable JSONL
                  </button>
                )}
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
              </>
            )}
          </div>
        </header>

        {/* Stats — two layouts depending on dataset kind */}
        {isEval ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <Stat label="rows" value={`${total}`} sub="total entered" />
            <Stat
              label="compile ok"
              value={`${nCompile}/${total}`}
              sub={
                total > 0
                  ? `${Math.round((nCompile / total) * 100)}%`
                  : undefined
              }
            />
            <Stat
              label="runtime ok"
              value={`${nRuntime}/${total}`}
              sub={
                total > 0
                  ? `${Math.round((nRuntime / total) * 100)}%`
                  : undefined
              }
            />
            <Stat
              label="trainable"
              value={`${nTrainable}/${total}`}
              sub="compile AND runtime"
            />
          </div>
        ) : (
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
              sub={
                cacheWrite > 0
                  ? `${(cacheWrite / 1000).toFixed(1)}K write`
                  : undefined
              }
            />
            <Stat
              label="submitted"
              value={fmtTime(dataset.submitted_at) || "—"}
              sub={
                dataset.completed_at ? fmtTime(dataset.completed_at) : undefined
              }
            />
          </div>
        )}

        {isEval && rows.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-zinc-500">filter:</span>
            <FilterChip
              label="compile ✓"
              active={fCompile === true}
              onClick={() =>
                setFCompile(fCompile === true ? undefined : true)
              }
            />
            <FilterChip
              label="compile ✗"
              active={fCompile === false}
              onClick={() =>
                setFCompile(fCompile === false ? undefined : false)
              }
            />
            <FilterChip
              label="runtime ✓"
              active={fRuntime === true}
              onClick={() =>
                setFRuntime(fRuntime === true ? undefined : true)
              }
            />
            <FilterChip
              label="runtime ✗"
              active={fRuntime === false}
              onClick={() =>
                setFRuntime(fRuntime === false ? undefined : false)
              }
            />
            {(fCompile !== undefined || fRuntime !== undefined) && (
              <button
                onClick={() => {
                  setFCompile(undefined);
                  setFRuntime(undefined);
                }}
                className="text-zinc-500 hover:text-zinc-200 underline-offset-2 hover:underline"
              >
                clear
              </button>
            )}
            <span className="ml-auto text-zinc-500">
              {visibleRows.length}/{rows.length} shown
            </span>
          </div>
        )}

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
                <th className="px-4 py-3 text-left font-medium">
                  {isEval ? "plugin / spec" : "user prompt"}
                </th>
                <th className="px-4 py-3 text-left font-medium">response</th>
                {isEval ? (
                  <>
                    <th className="px-4 py-3 text-center font-medium w-20">
                      compile
                    </th>
                    <th className="px-4 py-3 text-center font-medium w-20">
                      runtime
                    </th>
                    <th className="px-4 py-3 w-10"></th>
                  </>
                ) : (
                  <th className="px-4 py-3 text-right font-medium">tokens</th>
                )}
              </tr>
            </thead>
            <tbody>
              {visibleRows.length === 0 ? (
                <tr>
                  <td
                    colSpan={isEval ? 6 : 4}
                    className="px-4 py-12 text-center text-zinc-500"
                  >
                    {isEval
                      ? total === 0
                        ? "no rows yet — click Add row"
                        : "no rows match the current filter"
                      : dataset.status === "running"
                        ? "waiting for batch to complete…"
                        : "no rows yet"}
                  </td>
                </tr>
              ) : (
                visibleRows.map((r) => (
                  <tr
                    key={r.id}
                    onClick={() => setSelected(r)}
                    className="border-t border-zinc-800 hover:bg-zinc-900/30 cursor-pointer"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-zinc-500">
                      {r.row_index}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-300 max-w-xs truncate">
                      {isEval && r.meta?.plugin_name ? (
                        <>
                          <div className="text-zinc-200 font-medium">
                            {r.meta.plugin_name}
                          </div>
                          <div className="text-zinc-500 text-[10px] truncate">
                            {r.input.user.slice(0, 80)}
                          </div>
                        </>
                      ) : (
                        r.input.user
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-400 max-w-md truncate">
                      {r.error ? (
                        <span className="text-red-400">{r.error}</span>
                      ) : (
                        (r.output ?? "").slice(0, 160) ||
                        (isEval ? (
                          <span className="text-zinc-600 italic">
                            no output yet
                          </span>
                        ) : (
                          ""
                        ))
                      )}
                    </td>
                    {isEval ? (
                      <>
                        <td className="px-4 py-3 text-center">
                          <MetaBadge value={r.meta?.compile} />
                        </td>
                        <td className="px-4 py-3 text-center">
                          <MetaBadge value={r.meta?.runtime} />
                        </td>
                        <td
                          className="px-2 py-3 text-right"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button
                            onClick={() => deleteRow(r)}
                            className="text-zinc-500 hover:text-red-400"
                            title="Delete row"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </td>
                      </>
                    ) : (
                      <td className="px-4 py-3 text-right text-xs tabular-nums text-zinc-500">
                        {r.usage?.output_tokens ?? "—"}
                      </td>
                    )}
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

        {selected && (
          <RowDialog
            row={selected}
            datasetId={id}
            isEval={isEval}
            onClose={() => setSelected(null)}
            onSaved={() => {
              fetchRows();
              fetchDataset();
            }}
          />
        )}
        {adding && (
          <AddRowDialog
            datasetId={id}
            defaultSystem={dataset.config.system ?? ""}
            onClose={() => setAdding(false)}
            onAdded={() => {
              setAdding(false);
              fetchRows();
              fetchDataset();
            }}
          />
        )}
      </main>
    </AppShell>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full px-2.5 py-0.5 text-xs ring-1 ring-inset ${
        active
          ? "bg-zinc-100 text-zinc-900 ring-zinc-100"
          : "bg-zinc-900 text-zinc-400 ring-zinc-700 hover:text-zinc-200"
      }`}
    >
      {label}
    </button>
  );
}

function MetaBadge({ value }: { value: boolean | undefined }) {
  if (value === true)
    return (
      <Check className="h-4 w-4 text-green-400 inline" strokeWidth={3} />
    );
  if (value === false)
    return <X className="h-4 w-4 text-red-400 inline" strokeWidth={3} />;
  return <span className="text-zinc-600">—</span>;
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

function RowDialog({
  row,
  datasetId,
  isEval,
  onClose,
  onSaved,
}: {
  row: Row;
  datasetId: string;
  isEval: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [meta, setMeta] = useState<RowMeta>(row.meta ?? {});
  const [output, setOutput] = useState<string>(row.output ?? "");
  const [saving, setSaving] = useState(false);

  const copy = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("Copied");
  };

  const save = async () => {
    setSaving(true);
    try {
      await api(`/admin/datasets/${datasetId}/rows/${row.id}`, {
        method: "PATCH",
        body: JSON.stringify({ meta, output }),
      });
      toast.success("Saved");
      onSaved();
      onClose();
    } catch (e) {
      toast.error(`Save failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
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
          <h2 className="text-lg font-semibold">
            Row {row.row_index}
            {meta.plugin_name && (
              <span className="ml-2 text-zinc-400 font-normal text-sm">
                · {meta.plugin_name}
              </span>
            )}
          </h2>
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
          ) : isEval ? (
            <div>
              <div className="flex items-center justify-between mb-1">
                <div className="text-[10px] uppercase tracking-wider text-zinc-500">
                  Response (editable)
                </div>
                <button
                  onClick={() => copy(output)}
                  className="text-zinc-500 hover:text-zinc-200 flex items-center gap-1 text-[10px]"
                >
                  <Copy className="h-3 w-3" /> copy
                </button>
              </div>
              <textarea
                value={output}
                onChange={(e) => setOutput(e.target.value)}
                rows={10}
                className="w-full rounded border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs font-mono text-zinc-200"
              />
            </div>
          ) : (
            <Section label="Response" copyText={row.output ?? ""} onCopy={copy}>
              <pre className="whitespace-pre-wrap text-xs font-mono text-zinc-200">
                {row.output}
              </pre>
            </Section>
          )}

          {isEval && (
            <MetaEditor meta={meta} onChange={setMeta} />
          )}

          {row.usage && (
            <div className="rounded bg-zinc-900/50 p-3 text-xs text-zinc-400 grid grid-cols-2 gap-2">
              <div>input: {row.usage.input_tokens ?? 0}</div>
              <div>output: {row.usage.output_tokens ?? 0}</div>
              <div>cache read: {row.usage.cache_read_input_tokens ?? 0}</div>
              <div>
                cost:{" "}
                <span className="tabular-nums">
                  ${(row.usage.cost_usd ?? 0).toFixed(6)}
                </span>
              </div>
            </div>
          )}
        </div>

        {isEval && (
          <div className="mt-5 flex items-center justify-end gap-2">
            <button
              onClick={onClose}
              className="rounded px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200"
            >
              Cancel
            </button>
            <button
              onClick={save}
              disabled={saving}
              className="rounded bg-zinc-100 px-4 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function AddRowDialog({
  datasetId,
  defaultSystem,
  onClose,
  onAdded,
}: {
  datasetId: string;
  defaultSystem: string;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [system, setSystem] = useState(defaultSystem);
  const [user, setUser] = useState("");
  const [output, setOutput] = useState("");
  const [meta, setMeta] = useState<RowMeta>({});
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!user.trim()) {
      toast.error("User prompt (the spec) is required");
      return;
    }
    setSaving(true);
    try {
      await api(`/admin/datasets/${datasetId}/rows`, {
        method: "POST",
        body: JSON.stringify({
          system,
          user,
          output: output || null,
          meta,
        }),
      });
      toast.success("Row added");
      onAdded();
    } catch (e) {
      toast.error(`Add failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
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
        <h2 className="text-lg font-semibold mb-1">Add eval row</h2>
        <p className="text-xs text-zinc-500 mb-4">
          Paste the spec you sent and the raw model output. Flip compile/
          runtime flags after testing locally — you can always edit later.
        </p>

        <div className="space-y-3">
          <label className="block">
            <span className="text-xs uppercase tracking-wider text-zinc-500">
              System prompt
            </span>
            <textarea
              value={system}
              onChange={(e) => setSystem(e.target.value)}
              rows={3}
              placeholder="(default from dataset — edit per-row if you want)"
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
          </label>

          <label className="block">
            <span className="text-xs uppercase tracking-wider text-zinc-500">
              User prompt (spec) *
            </span>
            <textarea
              value={user}
              onChange={(e) => setUser(e.target.value)}
              rows={6}
              placeholder={`{"plugin_name": "...", "commands": [...] }`}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
          </label>

          <label className="block">
            <span className="text-xs uppercase tracking-wider text-zinc-500">
              Model output (the generated plugin code)
            </span>
            <textarea
              value={output}
              onChange={(e) => setOutput(e.target.value)}
              rows={8}
              placeholder="===== pom.xml ====&#10;..."
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
          </label>

          <MetaEditor meta={meta} onChange={setMeta} />
        </div>

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200"
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={saving || !user.trim()}
            className="rounded bg-zinc-100 px-4 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
          >
            {saving ? "Saving…" : "Add row"}
          </button>
        </div>
      </div>
    </div>
  );
}

function MetaEditor({
  meta,
  onChange,
}: {
  meta: RowMeta;
  onChange: (m: RowMeta) => void;
}) {
  const set = (patch: Partial<RowMeta>) => onChange({ ...meta, ...patch });
  return (
    <div className="rounded border border-zinc-800 bg-zinc-950 p-3 space-y-3">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">
        Eval metadata
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500">
            Plugin name
          </span>
          <input
            value={meta.plugin_name ?? ""}
            onChange={(e) => set({ plugin_name: e.target.value })}
            placeholder="RepairItem"
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
          />
        </label>
        <label className="block">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500">
            Complexity
          </span>
          <select
            value={meta.complexity ?? ""}
            onChange={(e) =>
              set({ complexity: (e.target.value || undefined) as RowMeta["complexity"] })
            }
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
          >
            <option value="">—</option>
            <option value="tiny">tiny (1 cmd, no events)</option>
            <option value="small">small (multi-cmd, no events)</option>
            <option value="medium">medium (1–2 events)</option>
            <option value="large">large (3–5 events)</option>
            <option value="xl">xl (6+ events)</option>
          </select>
        </label>
      </div>

      <div className="flex gap-4 text-sm">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={!!meta.compile}
            onChange={(e) => set({ compile: e.target.checked })}
            className="h-4 w-4 accent-green-500"
          />
          <span className="text-zinc-300">Compile OK</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={!!meta.runtime}
            onChange={(e) => set({ runtime: e.target.checked })}
            className="h-4 w-4 accent-green-500"
          />
          <span className="text-zinc-300">Runtime OK</span>
        </label>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500">
            Failure type
          </span>
          <input
            value={meta.failure_type ?? ""}
            onChange={(e) =>
              set({ failure_type: e.target.value || undefined })
            }
            placeholder="deprecated_api"
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
          />
        </label>
        <label className="block">
          <span className="text-[10px] uppercase tracking-wider text-zinc-500">
            Fix applied
          </span>
          <input
            value={meta.fix_applied ?? ""}
            onChange={(e) => set({ fix_applied: e.target.value || undefined })}
            placeholder="used Damageable"
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
          />
        </label>
      </div>

      <label className="block">
        <span className="text-[10px] uppercase tracking-wider text-zinc-500">
          Notes
        </span>
        <textarea
          value={meta.notes ?? ""}
          onChange={(e) => set({ notes: e.target.value || undefined })}
          rows={2}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs"
        />
      </label>
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
