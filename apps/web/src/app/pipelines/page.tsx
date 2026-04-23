"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, Edit3, Trash2, Copy } from "lucide-react";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

type Pipeline = {
  id: string;
  slug: string;
  label: string;
  model_slug: string;
  system_prompt: string | null;
  enabled: boolean;
  created_at: string;
};

type Model = { id: string; slug: string };

const EMPTY: Omit<Pipeline, "id" | "created_at"> = {
  slug: "",
  label: "",
  model_slug: "",
  system_prompt: "",
  enabled: true,
};

export default function PipelinesPage() {
  const [rows, setRows] = useState<Pipeline[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Pipeline | null>(null);
  const [creating, setCreating] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [pipes, mdls] = await Promise.all([
        api<Pipeline[]>("/admin/pipelines"),
        api<Model[]>("/admin/models"),
      ]);
      setRows(pipes);
      setModels(mdls);
    } catch (e) {
      toast.error(`Load failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const del = async (p: Pipeline) => {
    if (!confirm(`Delete pipeline "${p.slug}"?`)) return;
    await api(`/admin/pipelines/${p.id}`, { method: "DELETE" });
    toast.success("Deleted");
    fetchAll();
  };

  const toggle = async (p: Pipeline) => {
    await api(`/admin/pipelines/${p.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !p.enabled }),
    });
    fetchAll();
  };

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-semibold">Pipelines</h1>
            <p className="text-sm text-zinc-500 mt-1">
              Named presets — model + system prompt. Clients POST to{" "}
              <code className="text-zinc-300">
                /v1/pipelines/&lt;slug&gt;/chat
              </code>
              .
            </p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 rounded bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200"
          >
            <Plus className="h-3.5 w-3.5" /> Add pipeline
          </button>
        </header>

        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3 text-left font-medium">slug</th>
                <th className="px-4 py-3 text-left font-medium">label</th>
                <th className="px-4 py-3 text-left font-medium">model</th>
                <th className="px-4 py-3 text-left font-medium">system</th>
                <th className="px-4 py-3 text-left font-medium">enabled</th>
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
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-zinc-500">
                    no pipelines — click <b>Add pipeline</b>
                  </td>
                </tr>
              ) : (
                rows.map((p) => (
                  <tr
                    key={p.id}
                    className="border-t border-zinc-800 hover:bg-zinc-900/30"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-zinc-100">
                      {p.slug}
                    </td>
                    <td className="px-4 py-3 text-xs">{p.label}</td>
                    <td className="px-4 py-3 font-mono text-xs text-zinc-400">
                      {p.model_slug}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-400 max-w-xs truncate">
                      {p.system_prompt || "(none)"}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => toggle(p)}
                        className={`text-xs rounded px-2 py-0.5 ring-1 ring-inset ${
                          p.enabled
                            ? "bg-green-950/60 text-green-300 ring-green-900"
                            : "bg-zinc-800 text-zinc-500 ring-zinc-700"
                        }`}
                      >
                        {p.enabled ? "enabled" : "disabled"}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap">
                      <button
                        onClick={() => {
                          const path = `/v1/pipelines/${p.slug}/chat`;
                          navigator.clipboard.writeText(path);
                          toast.success(`Copied: ${path}`);
                        }}
                        className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800 mr-1"
                        title="Copy endpoint path"
                      >
                        <Copy className="h-3 w-3" />
                      </button>
                      <button
                        onClick={() => setEditing(p)}
                        className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800 mr-1"
                      >
                        <Edit3 className="h-3 w-3" />
                      </button>
                      <button
                        onClick={() => del(p)}
                        className="rounded border border-red-900 bg-red-950/40 px-2 py-1 text-xs text-red-300 hover:bg-red-950/70"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {creating && (
          <Dialog
            initial={EMPTY}
            models={models}
            title="Add pipeline"
            onClose={() => setCreating(false)}
            onSave={async (d) => {
              await api("/admin/pipelines", {
                method: "POST",
                body: JSON.stringify(d),
              });
              toast.success("Pipeline added");
              setCreating(false);
              fetchAll();
            }}
          />
        )}
        {editing && (
          <Dialog
            initial={editing}
            models={models}
            title={`Edit ${editing.slug}`}
            onClose={() => setEditing(null)}
            onSave={async (d) => {
              await api(`/admin/pipelines/${editing.id}`, {
                method: "PATCH",
                body: JSON.stringify(d),
              });
              toast.success("Updated");
              setEditing(null);
              fetchAll();
            }}
          />
        )}
      </main>
    </AppShell>
  );
}

function Dialog({
  initial,
  models,
  title,
  onSave,
  onClose,
}: {
  initial: Partial<Pipeline>;
  models: Model[];
  title: string;
  onSave: (d: Partial<Pipeline>) => Promise<void>;
  onClose: () => void;
}) {
  const [slug, setSlug] = useState(initial.slug ?? "");
  const [label, setLabel] = useState(initial.label ?? "");
  const [modelSlug, setModelSlug] = useState(
    initial.model_slug ?? models[0]?.slug ?? "",
  );
  const [systemPrompt, setSystemPrompt] = useState(
    initial.system_prompt ?? "",
  );
  const [enabled, setEnabled] = useState(initial.enabled ?? true);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await onSave({
        slug,
        label,
        model_slug: modelSlug,
        system_prompt: systemPrompt || null,
        enabled,
      });
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
        className="w-full max-w-xl rounded-xl border border-zinc-800 bg-zinc-950 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-4">{title}</h2>
        <div className="space-y-3">
          <F label="Slug" hint="url-safe, e.g. customer-support-v1">
            <input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm font-mono"
            />
          </F>
          <F label="Label">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
            />
          </F>
          <F label="Model">
            <select
              value={modelSlug}
              onChange={(e) => setModelSlug(e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
            >
              {models.map((m) => (
                <option key={m.id} value={m.slug}>
                  {m.slug}
                </option>
              ))}
            </select>
          </F>
          <F label="System prompt">
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={5}
              placeholder="You are a helpful..."
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm font-mono"
            />
          </F>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="h-4 w-4 accent-zinc-200"
            />
            Enabled
          </label>
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
            disabled={saving || !slug || !label || !modelSlug}
            className="rounded bg-zinc-100 px-4 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function F({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {children}
      {hint && <span className="text-[10px] text-zinc-500 mt-0.5 block">{hint}</span>}
    </label>
  );
}
