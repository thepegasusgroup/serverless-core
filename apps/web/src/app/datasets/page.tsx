"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Plus, Trash2, XCircle, Download, Send } from "lucide-react";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";
import { createClient } from "@/lib/supabase/client";
import { fmtTime } from "@/lib/time";

type Dataset = {
  id: string;
  slug: string;
  label: string;
  status:
    | "draft"
    | "submitting"
    | "running"
    | "completed"
    | "failed"
    | "canceled";
  provider: string;
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
  submitted_at: string | null;
  completed_at: string | null;
  created_at: string;
};

const MODELS = [
  { id: "claude-opus-4-7", label: "Opus 4.7 (highest quality, $2.50/$12.50 batch)" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6 ($1.50/$7.50 batch)" },
  { id: "claude-haiku-4-5", label: "Haiku 4.5 (cheapest, $0.50/$2.50 batch)" },
];

function StatusBadge({ status }: { status: Dataset["status"] }) {
  const styles: Record<Dataset["status"], string> = {
    draft: "bg-zinc-800 text-zinc-400 ring-zinc-700",
    submitting: "bg-blue-950/60 text-blue-300 ring-blue-900",
    running: "bg-blue-950/60 text-blue-300 ring-blue-900",
    completed: "bg-green-950/60 text-green-300 ring-green-900",
    failed: "bg-red-950/60 text-red-300 ring-red-900",
    canceled: "bg-zinc-800 text-zinc-400 ring-zinc-700",
  };
  return (
    <span
      className={`text-xs rounded px-2 py-0.5 ring-1 ring-inset ${styles[status]}`}
    >
      {status}
    </span>
  );
}

export default function DatasetsPage() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const data = await api<Dataset[]>("/admin/datasets");
      setDatasets(data);
    } catch (e) {
      toast.error(`Load failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const supabase = createClient();
    const channel = supabase
      .channel("datasets-live")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "datasets" },
        () => fetchAll(),
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, [fetchAll]);

  const cancel = async (d: Dataset) => {
    if (!confirm(`Cancel batch "${d.slug}"? Partial results are kept.`))
      return;
    try {
      await api(`/admin/datasets/${d.id}/cancel`, { method: "POST" });
      toast.success("Canceled");
      fetchAll();
    } catch (e) {
      toast.error(`Cancel failed: ${(e as Error).message}`);
    }
  };

  const del = async (d: Dataset) => {
    if (
      !confirm(
        `Delete "${d.slug}" and all its rows? This can't be undone.`,
      )
    )
      return;
    try {
      await api(`/admin/datasets/${d.id}`, { method: "DELETE" });
      toast.success("Deleted");
      fetchAll();
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
    }
  };

  const submit = async (d: Dataset) => {
    try {
      await api(`/admin/datasets/${d.id}/submit`, { method: "POST" });
      toast.success("Submitted to Anthropic");
      fetchAll();
    } catch (e) {
      toast.error(`Submit failed: ${(e as Error).message}`);
    }
  };

  const download = async (d: Dataset, fmt: "jsonl" | "csv") => {
    const supabase = createClient();
    const { data: { session } } = await supabase.auth.getSession();
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
    const url = `${apiUrl}/admin/datasets/${d.id}/export?fmt=${fmt}`;
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
    a.download = `${d.slug}.${fmt}`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <header className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-3xl font-semibold">Datasets</h1>
            <p className="text-sm text-zinc-500 mt-1">
              Generate synthetic datasets via the Claude Batch API — 50% off
              standard pricing, with prompt caching on the shared system prompt.
            </p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 rounded bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200"
          >
            <Plus className="h-3.5 w-3.5" /> New dataset
          </button>
        </header>

        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3 text-left font-medium">label</th>
                <th className="px-4 py-3 text-left font-medium">status</th>
                <th className="px-4 py-3 text-right font-medium">progress</th>
                <th className="px-4 py-3 text-right font-medium">cost</th>
                <th className="px-4 py-3 text-left font-medium">submitted</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-zinc-500">
                    loading…
                  </td>
                </tr>
              ) : datasets.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-zinc-500">
                    no datasets yet — click <b>New dataset</b>
                  </td>
                </tr>
              ) : (
                datasets.map((d) => {
                  const pct =
                    d.progress_total > 0
                      ? Math.round(
                          ((d.progress_completed + d.progress_errored) /
                            d.progress_total) *
                            100,
                        )
                      : 0;
                  const cost = d.usage?.cost_usd ?? 0;
                  const active =
                    d.status === "submitting" || d.status === "running";
                  return (
                    <tr
                      key={d.id}
                      className="border-t border-zinc-800 hover:bg-zinc-900/30"
                    >
                      <td className="px-4 py-3">
                        <Link
                          href={`/datasets/${d.id}`}
                          className="font-medium text-zinc-100 hover:underline"
                        >
                          {d.label}
                        </Link>
                        <div className="font-mono text-[11px] text-zinc-500">
                          {d.slug}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={d.status} />
                        {d.error && (
                          <div className="text-[10px] text-red-400 mt-1 max-w-xs truncate">
                            {d.error}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right text-xs tabular-nums">
                        <div className="text-zinc-200">
                          {d.progress_completed + d.progress_errored}/
                          {d.progress_total}
                        </div>
                        <div className="text-[10px] text-zinc-500">{pct}%</div>
                      </td>
                      <td className="px-4 py-3 text-right text-xs tabular-nums text-zinc-300">
                        {cost > 0 ? `$${cost.toFixed(4)}` : "—"}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-400 tabular-nums">
                        {fmtTime(d.submitted_at)}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        {d.status === "draft" && (
                          <button
                            onClick={() => submit(d)}
                            className="rounded border border-blue-900 bg-blue-950/40 px-2.5 py-1 text-xs text-blue-300 hover:bg-blue-950/70 mr-1"
                            title="Submit to Anthropic"
                          >
                            <Send className="h-3 w-3 inline" /> submit
                          </button>
                        )}
                        {d.status === "completed" && (
                          <>
                            <button
                              onClick={() => download(d, "jsonl")}
                              className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800 mr-1"
                              title="Download JSONL"
                            >
                              <Download className="h-3 w-3 inline" /> jsonl
                            </button>
                            <button
                              onClick={() => download(d, "csv")}
                              className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800 mr-1"
                              title="Download CSV"
                            >
                              <Download className="h-3 w-3 inline" /> csv
                            </button>
                          </>
                        )}
                        {active && (
                          <button
                            onClick={() => cancel(d)}
                            className="rounded border border-amber-900 bg-amber-950/40 px-2 py-1 text-xs text-amber-300 hover:bg-amber-950/70 mr-1"
                            title="Cancel"
                          >
                            <XCircle className="h-3 w-3" />
                          </button>
                        )}
                        <button
                          onClick={() => del(d)}
                          className="rounded border border-red-900 bg-red-950/40 px-2 py-1 text-xs text-red-300 hover:bg-red-950/70"
                          title="Delete"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {creating && (
          <CreateDatasetDialog
            onClose={() => setCreating(false)}
            onCreated={() => {
              setCreating(false);
              fetchAll();
            }}
          />
        )}
      </main>
    </AppShell>
  );
}

function CreateDatasetDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [slug, setSlug] = useState("");
  const [label, setLabel] = useState("");
  const [model, setModel] = useState(MODELS[0].id);
  const [system, setSystem] = useState("");
  const [promptsText, setPromptsText] = useState("");
  const [maxTokens, setMaxTokens] = useState(4096);
  const [cacheSystem, setCacheSystem] = useState(true);
  const [submitNow, setSubmitNow] = useState(true);
  const [saving, setSaving] = useState(false);

  const promptList = promptsText
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

  const save = async () => {
    if (!slug.trim() || !label.trim() || promptList.length === 0) {
      toast.error("Slug, label, and at least one prompt are required");
      return;
    }
    setSaving(true);
    try {
      await api("/admin/datasets", {
        method: "POST",
        body: JSON.stringify({
          slug: slug.trim(),
          label: label.trim(),
          model,
          system,
          prompts: promptList,
          max_tokens: maxTokens,
          cache_system: cacheSystem,
          submit_now: submitNow,
        }),
      });
      toast.success(
        submitNow
          ? "Created and submitted to Anthropic"
          : "Saved as draft",
      );
      onCreated();
    } catch (e) {
      toast.error((e as Error).message);
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
        className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl border border-zinc-800 bg-zinc-950 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-4">New dataset</h2>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs uppercase tracking-wider text-zinc-500">
                Slug
              </span>
              <input
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="mc-planner-v1"
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm font-mono"
              />
              <span className="text-[10px] text-zinc-500 mt-0.5 block">
                lowercase, dashes ok
              </span>
            </label>
            <label className="block">
              <span className="text-xs uppercase tracking-wider text-zinc-500">
                Label
              </span>
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="MC Plugin Planner v1"
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
              />
            </label>
          </div>

          <label className="block">
            <span className="text-xs uppercase tracking-wider text-zinc-500">
              Model
            </span>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
            >
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs uppercase tracking-wider text-zinc-500">
              System prompt (shared across every row, cached after first request)
            </span>
            <textarea
              value={system}
              onChange={(e) => setSystem(e.target.value)}
              rows={8}
              placeholder="You are a Minecraft Plugin Planner. …"
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
          </label>

          <label className="block">
            <span className="text-xs uppercase tracking-wider text-zinc-500">
              User prompts ({promptList.length} rows) — one per line
            </span>
            <textarea
              value={promptsText}
              onChange={(e) => setPromptsText(e.target.value)}
              rows={10}
              placeholder={`Make a /fly command that toggles flight mode\nCreate a /heal command that restores health\n...`}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
            <span className="text-[10px] text-zinc-500 mt-0.5 block">
              Up to 5000 rows. Blank lines ignored.
            </span>
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs uppercase tracking-wider text-zinc-500">
                Max tokens per response
              </span>
              <input
                type="number"
                value={maxTokens}
                onChange={(e) => setMaxTokens(parseInt(e.target.value) || 4096)}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
              />
            </label>
            <div className="flex flex-col justify-end gap-2 text-sm">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={cacheSystem}
                  onChange={(e) => setCacheSystem(e.target.checked)}
                  className="h-4 w-4 accent-zinc-200"
                />
                Cache system prompt (~90% cheaper on repeats)
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={submitNow}
                  onChange={(e) => setSubmitNow(e.target.checked)}
                  className="h-4 w-4 accent-zinc-200"
                />
                Submit immediately
              </label>
            </div>
          </div>
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
            disabled={saving || !slug || !label || promptList.length === 0}
            className="rounded bg-zinc-100 px-4 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
          >
            {saving
              ? "Saving…"
              : submitNow
                ? "Create + submit"
                : "Save as draft"}
          </button>
        </div>
      </div>
    </div>
  );
}
