"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Copy, Check, Plus, Edit3 } from "lucide-react";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

type ApiKey = {
  id: string;
  label: string;
  prefix: string;
  requests_per_minute: number | null;
  allowed_models: string[] | null;
  allowed_pipelines: string[] | null;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

type Model = { id: string; slug: string };
type Pipeline = { id: string; slug: string; label: string };

type CreateResp = ApiKey & { key: string };

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [revealedKey, setRevealedKey] = useState<CreateResp | null>(null);
  const [copied, setCopied] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ApiKey | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [k, m, p] = await Promise.all([
        api<ApiKey[]>("/admin/api-keys"),
        api<Model[]>("/admin/models"),
        api<Pipeline[]>("/admin/pipelines"),
      ]);
      setKeys(k);
      setModels(m);
      setPipelines(p);
    } catch (e) {
      toast.error(`Load failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const revoke = async (id: string, label: string) => {
    if (!confirm(`Revoke "${label}"?`)) return;
    try {
      await api(`/admin/api-keys/${id}`, { method: "DELETE" });
      toast.success("Revoked");
      fetchAll();
    } catch (e) {
      toast.error(`Revoke failed: ${(e as Error).message}`);
    }
  };

  const copy = async () => {
    if (!revealedKey) return;
    await navigator.clipboard.writeText(revealedKey.key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-semibold">API keys</h1>
            <p className="text-sm text-zinc-500 mt-1">
              For clients hitting{" "}
              <code className="text-zinc-300">/v1/*</code>. Scope each key to
              the models and pipelines you want it to access.
            </p>
          </div>
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 rounded bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200"
          >
            <Plus className="h-3.5 w-3.5" /> Create key
          </button>
        </header>

        {revealedKey && (
          <section className="mb-6 rounded-xl border border-yellow-900/60 bg-yellow-950/30 p-5">
            <div className="text-sm text-yellow-300 font-medium mb-1">
              Save this key — it's only shown once
            </div>
            <div className="flex items-center gap-2 mt-2">
              <code className="flex-1 rounded bg-zinc-950 border border-zinc-800 px-3 py-2 font-mono text-xs text-zinc-100 overflow-x-auto">
                {revealedKey.key}
              </code>
              <button
                onClick={copy}
                className="flex items-center gap-1.5 rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs hover:bg-zinc-800"
              >
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copied ? "Copied" : "Copy"}
              </button>
              <button
                onClick={() => setRevealedKey(null)}
                className="rounded px-2 py-2 text-xs text-zinc-500 hover:text-zinc-200"
              >
                Dismiss
              </button>
            </div>
          </section>
        )}

        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3 text-left font-medium">label</th>
                <th className="px-4 py-3 text-left font-medium">prefix</th>
                <th className="px-4 py-3 text-right font-medium">req/min</th>
                <th className="px-4 py-3 text-left font-medium">scope</th>
                <th className="px-4 py-3 text-left font-medium">last used</th>
                <th className="px-4 py-3 text-left font-medium">status</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-zinc-500">
                    loading…
                  </td>
                </tr>
              ) : keys.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-zinc-500">
                    no keys — click <b>Create key</b>
                  </td>
                </tr>
              ) : (
                keys.map((k) => {
                  const revoked = !!k.revoked_at;
                  const scopeSummary = summarize(
                    k.allowed_models,
                    k.allowed_pipelines,
                  );
                  return (
                    <tr
                      key={k.id}
                      className="border-t border-zinc-800 hover:bg-zinc-900/30"
                    >
                      <td className="px-4 py-3">{k.label}</td>
                      <td className="px-4 py-3 font-mono text-xs text-zinc-400">
                        {k.prefix}…
                      </td>
                      <td className="px-4 py-3 text-right text-xs tabular-nums">
                        {k.requests_per_minute ?? "∞"}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-400">
                        {scopeSummary}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-400">
                        {k.last_used_at
                          ? new Date(k.last_used_at).toLocaleString()
                          : "never"}
                      </td>
                      <td className="px-4 py-3">
                        {revoked ? (
                          <span className="text-red-400 text-xs">revoked</span>
                        ) : (
                          <span className="text-green-400 text-xs">active</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap">
                        {!revoked && (
                          <>
                            <button
                              onClick={() => setEditing(k)}
                              className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-800 mr-1"
                              title="Edit scopes + rate limit"
                            >
                              <Edit3 className="h-3 w-3" />
                            </button>
                            <button
                              onClick={() => revoke(k.id, k.label)}
                              className="rounded border border-red-900 bg-red-950/40 px-2.5 py-1 text-xs font-medium text-red-300 hover:bg-red-950/70"
                            >
                              revoke
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {creating && (
          <KeyDialog
            mode="create"
            initial={null}
            models={models}
            pipelines={pipelines}
            title="Create API key"
            onClose={() => setCreating(false)}
            onSave={async (payload) => {
              const resp = await api<CreateResp>("/admin/api-keys", {
                method: "POST",
                body: JSON.stringify(payload),
              });
              setRevealedKey(resp);
              setCreating(false);
              toast.success("Key created — copy it now");
              fetchAll();
            }}
          />
        )}
        {editing && (
          <KeyDialog
            mode="edit"
            initial={editing}
            models={models}
            pipelines={pipelines}
            title={`Edit ${editing.label}`}
            onClose={() => setEditing(null)}
            onSave={async (payload) => {
              await api(`/admin/api-keys/${editing.id}`, {
                method: "PATCH",
                body: JSON.stringify(payload),
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

function summarize(
  models: string[] | null,
  pipes: string[] | null,
): React.ReactNode {
  const parts: string[] = [];
  if (models === null) parts.push("all models");
  else if (models.length === 0) parts.push("no models");
  else parts.push(`${models.length} model${models.length > 1 ? "s" : ""}`);
  if (pipes === null) parts.push("all pipelines");
  else if (pipes.length === 0) parts.push("no pipelines");
  else parts.push(`${pipes.length} pipeline${pipes.length > 1 ? "s" : ""}`);
  return parts.join(" · ");
}

function KeyDialog({
  mode,
  initial,
  models,
  pipelines,
  title,
  onSave,
  onClose,
}: {
  mode: "create" | "edit";
  initial: ApiKey | null;
  models: Model[];
  pipelines: Pipeline[];
  title: string;
  onSave: (p: {
    label?: string;
    requests_per_minute: number | null;
    allowed_models: string[] | null;
    allowed_pipelines: string[] | null;
  }) => Promise<void>;
  onClose: () => void;
}) {
  const [label, setLabel] = useState(initial?.label ?? "");
  const [rpm, setRpm] = useState(
    initial?.requests_per_minute !== null &&
      initial?.requests_per_minute !== undefined
      ? String(initial.requests_per_minute)
      : "",
  );
  const [modelsMode, setModelsMode] = useState<"all" | "scope">(
    initial === null || initial.allowed_models === null ? "all" : "scope",
  );
  const [pipesMode, setPipesMode] = useState<"all" | "scope">(
    initial === null || initial.allowed_pipelines === null ? "all" : "scope",
  );
  const [modelSet, setModelSet] = useState<Set<string>>(
    new Set(initial?.allowed_models ?? []),
  );
  const [pipeSet, setPipeSet] = useState<Set<string>>(
    new Set(initial?.allowed_pipelines ?? []),
  );
  const [saving, setSaving] = useState(false);

  const toggleModel = (slug: string) => {
    const next = new Set(modelSet);
    next.has(slug) ? next.delete(slug) : next.add(slug);
    setModelSet(next);
  };
  const togglePipe = (slug: string) => {
    const next = new Set(pipeSet);
    next.has(slug) ? next.delete(slug) : next.add(slug);
    setPipeSet(next);
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload: {
        label?: string;
        requests_per_minute: number | null;
        allowed_models: string[] | null;
        allowed_pipelines: string[] | null;
      } = {
        requests_per_minute: rpm.trim() ? parseInt(rpm) : null,
        allowed_models: modelsMode === "all" ? null : [...modelSet],
        allowed_pipelines: pipesMode === "all" ? null : [...pipeSet],
      };
      if (mode === "create") payload.label = label;
      await onSave(payload);
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
        className="w-full max-w-xl rounded-xl border border-zinc-800 bg-zinc-950 p-5 max-h-[90vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-4">{title}</h2>
        <div className="space-y-4">
          {mode === "create" && (
            <F label="Label">
              <input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="e.g. production-backend"
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
              />
            </F>
          )}
          <F label="Requests / minute" hint="Leave empty for unlimited">
            <input
              value={rpm}
              onChange={(e) => setRpm(e.target.value)}
              placeholder="unlimited"
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
            />
          </F>

          <ScopeSection
            title="Models"
            mode={modelsMode}
            onModeChange={setModelsMode}
            items={models.map((m) => m.slug)}
            selected={modelSet}
            onToggle={toggleModel}
          />

          <ScopeSection
            title="Pipelines"
            mode={pipesMode}
            onModeChange={setPipesMode}
            items={pipelines.map((p) => p.slug)}
            selected={pipeSet}
            onToggle={togglePipe}
          />
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
            disabled={saving || (mode === "create" && !label)}
            className="rounded bg-zinc-100 px-4 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
          >
            {saving ? "Saving…" : mode === "create" ? "Create" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ScopeSection({
  title,
  mode,
  onModeChange,
  items,
  selected,
  onToggle,
}: {
  title: string;
  mode: "all" | "scope";
  onModeChange: (m: "all" | "scope") => void;
  items: string[];
  selected: Set<string>;
  onToggle: (s: string) => void;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs uppercase tracking-wider text-zinc-400">
          {title}
        </span>
        <div className="flex items-center gap-1 text-xs">
          <button
            onClick={() => onModeChange("all")}
            className={`px-2 py-0.5 rounded ${
              mode === "all"
                ? "bg-zinc-100 text-zinc-900"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            all
          </button>
          <button
            onClick={() => onModeChange("scope")}
            className={`px-2 py-0.5 rounded ${
              mode === "scope"
                ? "bg-zinc-100 text-zinc-900"
                : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            restrict
          </button>
        </div>
      </div>
      {mode === "scope" ? (
        items.length === 0 ? (
          <div className="text-xs text-zinc-500 italic">
            no {title.toLowerCase()} available
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-1 max-h-40 overflow-auto">
            {items.map((s) => (
              <label
                key={s}
                className="flex items-center gap-2 text-xs font-mono cursor-pointer py-0.5"
              >
                <input
                  type="checkbox"
                  checked={selected.has(s)}
                  onChange={() => onToggle(s)}
                  className="h-3.5 w-3.5 accent-zinc-200"
                />
                {s}
              </label>
            ))}
          </div>
        )
      ) : (
        <div className="text-xs text-zinc-500">
          Key can call any {title.toLowerCase()}.
        </div>
      )}
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
