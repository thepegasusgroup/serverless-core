"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, Edit3, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

type Model = {
  id: string;
  slug: string;
  hf_repo: string;
  vllm_args: Record<string, unknown>;
  min_vram_gb: number;
  docker_image: string;
  enabled: boolean;
  auto_pause_minutes: number | null;
  created_at: string;
};

const EMPTY: Omit<Model, "id" | "created_at"> = {
  slug: "",
  hf_repo: "",
  vllm_args: { max_model_len: 8192, dtype: "bfloat16", gpu_memory_utilization: 0.9 },
  min_vram_gb: 16,
  docker_image: "",
  enabled: true,
  auto_pause_minutes: 10,
};

export default function ModelsPage() {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Model | null>(null);
  const [creating, setCreating] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const data = await api<Model[]>("/admin/models");
      setModels(data);
    } catch (e) {
      toast.error(`Load failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const del = async (m: Model) => {
    if (!confirm(`Delete model "${m.slug}"? Instances using it may fail.`))
      return;
    try {
      await api(`/admin/models/${m.id}`, { method: "DELETE" });
      toast.success("Deleted");
      fetchAll();
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
    }
  };

  const toggleEnabled = async (m: Model) => {
    try {
      await api(`/admin/models/${m.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !m.enabled }),
      });
      fetchAll();
    } catch (e) {
      toast.error(`Toggle failed: ${(e as Error).message}`);
    }
  };

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-semibold">Models</h1>
            <p className="text-sm text-zinc-500 mt-1">
              Catalogue of LLMs clients can call via{" "}
              <code className="text-zinc-300">/v1/chat/completions</code>.
            </p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 rounded bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200"
          >
            <Plus className="h-3.5 w-3.5" /> Add model
          </button>
        </header>

        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3 text-left font-medium">slug</th>
                <th className="px-4 py-3 text-left font-medium">hf_repo</th>
                <th className="px-4 py-3 text-right font-medium">vram</th>
                <th className="px-4 py-3 text-right font-medium">idle pause</th>
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
              ) : models.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-zinc-500">
                    no models — click <b>Add model</b>
                  </td>
                </tr>
              ) : (
                models.map((m) => (
                  <tr
                    key={m.id}
                    className="border-t border-zinc-800 hover:bg-zinc-900/30"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-zinc-100">
                      {m.slug}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-zinc-400">
                      {m.hf_repo}
                    </td>
                    <td className="px-4 py-3 text-right text-xs tabular-nums">
                      {m.min_vram_gb}GB
                    </td>
                    <td className="px-4 py-3 text-right text-xs tabular-nums text-zinc-400">
                      {m.auto_pause_minutes
                        ? `${m.auto_pause_minutes}m`
                        : "never"}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => toggleEnabled(m)}
                        className={`text-xs rounded px-2 py-0.5 ring-1 ring-inset ${
                          m.enabled
                            ? "bg-green-950/60 text-green-300 ring-green-900"
                            : "bg-zinc-800 text-zinc-500 ring-zinc-700"
                        }`}
                      >
                        {m.enabled ? "enabled" : "disabled"}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap">
                      <button
                        onClick={() => setEditing(m)}
                        className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800 mr-1"
                        title="Edit"
                      >
                        <Edit3 className="h-3 w-3" />
                      </button>
                      <button
                        onClick={() => del(m)}
                        className="rounded border border-red-900 bg-red-950/40 px-2 py-1 text-xs text-red-300 hover:bg-red-950/70"
                        title="Delete"
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
          <ModelDialog
            initial={EMPTY}
            title="Add model"
            onClose={() => setCreating(false)}
            onSave={async (data) => {
              await api("/admin/models", {
                method: "POST",
                body: JSON.stringify(data),
              });
              toast.success("Model added");
              setCreating(false);
              fetchAll();
            }}
          />
        )}
        {editing && (
          <ModelDialog
            initial={editing}
            title={`Edit ${editing.slug}`}
            onClose={() => setEditing(null)}
            onSave={async (data) => {
              await api(`/admin/models/${editing.id}`, {
                method: "PATCH",
                body: JSON.stringify(data),
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

function ModelDialog({
  initial,
  title,
  onSave,
  onClose,
}: {
  initial: Partial<Model>;
  title: string;
  onSave: (data: Partial<Model>) => Promise<void>;
  onClose: () => void;
}) {
  const [slug, setSlug] = useState(initial.slug ?? "");
  const [hfRepo, setHfRepo] = useState(initial.hf_repo ?? "");
  const [vllmArgs, setVllmArgs] = useState(
    JSON.stringify(initial.vllm_args ?? {}, null, 2),
  );
  const [minVram, setMinVram] = useState(initial.min_vram_gb ?? 16);
  const [dockerImage, setDockerImage] = useState(initial.docker_image ?? "");
  const [enabled, setEnabled] = useState(initial.enabled ?? true);
  const [autoPause, setAutoPause] = useState(
    initial.auto_pause_minutes ?? 10,
  );
  const [saving, setSaving] = useState(false);

  const save = async () => {
    let parsedArgs: Record<string, unknown>;
    try {
      parsedArgs = JSON.parse(vllmArgs || "{}");
    } catch (e) {
      toast.error(`vllm_args is not valid JSON: ${(e as Error).message}`);
      return;
    }
    setSaving(true);
    try {
      await onSave({
        slug,
        hf_repo: hfRepo,
        vllm_args: parsedArgs,
        min_vram_gb: minVram,
        docker_image: dockerImage,
        enabled,
        auto_pause_minutes: autoPause,
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
          <Field label="Slug (used by clients)" hint="e.g. qwen2.5-7b-instruct">
            <input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm font-mono"
            />
          </Field>
          <Field label="HF repo" hint="e.g. Qwen/Qwen2.5-7B-Instruct">
            <input
              value={hfRepo}
              onChange={(e) => setHfRepo(e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm font-mono"
            />
          </Field>
          <Field label="Docker image">
            <input
              value={dockerImage}
              onChange={(e) => setDockerImage(e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm font-mono"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Min VRAM (GB)">
              <input
                type="number"
                value={minVram}
                onChange={(e) => setMinVram(parseInt(e.target.value) || 0)}
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
              />
            </Field>
            <Field label="Idle auto-pause (min)" hint="0 or empty = never">
              <input
                type="number"
                value={autoPause ?? ""}
                onChange={(e) =>
                  setAutoPause(
                    e.target.value === "" ? 0 : parseInt(e.target.value) || 0,
                  )
                }
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
              />
            </Field>
          </div>
          <Field label="vLLM args (JSON)">
            <textarea
              value={vllmArgs}
              onChange={(e) => setVllmArgs(e.target.value)}
              rows={5}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
          </Field>
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
            disabled={saving || !slug || !hfRepo || !dockerImage}
            className="rounded bg-zinc-100 px-4 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
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
