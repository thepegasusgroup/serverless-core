"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Plus,
  Edit3,
  Trash2,
  Copy,
  ArrowUp,
  ArrowDown,
  Cpu,
  Wand2,
} from "lucide-react";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

type Step =
  | {
      kind: "model";
      model_slug: string;
      system?: string;
      user_template?: string;
      vllm_overrides?: Record<string, unknown>;
      response_format?: "text" | "json_object" | "json_schema";
      response_schema?: Record<string, unknown> | null;
    }
  | {
      kind: "transform";
      transform: string;
      params?: Record<string, unknown>;
    };

type Pipeline = {
  id: string;
  slug: string;
  label: string;
  steps: Step[];
  output_mode: "return" | "webhook" | "json_only";
  webhook_url: string | null;
  webhook_headers: Record<string, string> | null;
  timeout_seconds: number | null;
  enabled: boolean;
  created_at: string;
};

type Model = { id: string; slug: string };

const TRANSFORM_KINDS = [
  { value: "trim", label: "Trim whitespace" },
  { value: "collapse_whitespace", label: "Collapse whitespace" },
  { value: "strip_markdown_fences", label: "Strip ``` fences" },
  { value: "extract_code_block", label: "Extract first code block" },
  { value: "extract_json", label: "Extract first JSON object/array" },
  { value: "regex_replace", label: "Regex replace" },
  { value: "replace", label: "Literal find & replace" },
  { value: "strip_prefix", label: "Strip prefix" },
  { value: "strip_suffix", label: "Strip suffix" },
] as const;

const OUTPUT_MODES = [
  { value: "return", label: "Return to caller" },
  { value: "webhook", label: "POST to webhook" },
  { value: "json_only", label: "Parse final as JSON" },
] as const;

export default function PipelinesPage() {
  const [rows, setRows] = useState<Pipeline[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Pipeline | null>(null);
  const [creating, setCreating] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [p, m] = await Promise.all([
        api<Pipeline[]>("/admin/pipelines"),
        api<Model[]>("/admin/models"),
      ]);
      setRows(p);
      setModels(m);
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
              Chain models and text transforms. Clients POST to{" "}
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
                <th className="px-4 py-3 text-left font-medium">steps</th>
                <th className="px-4 py-3 text-left font-medium">output</th>
                <th className="px-4 py-3 text-left font-medium">status</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-zinc-500">
                    loading…
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-zinc-500">
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
                      <div className="text-[10px] text-zinc-500 mt-0.5">
                        {p.label}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-400">
                      {(p.steps ?? []).length} step
                      {(p.steps ?? []).length === 1 ? "" : "s"}
                      <div className="text-[10px] text-zinc-600 mt-0.5">
                        {(p.steps ?? [])
                          .map((s) =>
                            s.kind === "model"
                              ? s.model_slug
                              : `⚙ ${s.transform}`,
                          )
                          .join("  →  ")}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs">
                      {OUTPUT_MODES.find((o) => o.value === p.output_mode)
                        ?.label ?? p.output_mode}
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
            initial={null}
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
  initial: Pipeline | null;
  models: Model[];
  title: string;
  onSave: (d: Partial<Pipeline>) => Promise<void>;
  onClose: () => void;
}) {
  const [slug, setSlug] = useState(initial?.slug ?? "");
  const [label, setLabel] = useState(initial?.label ?? "");
  const [steps, setSteps] = useState<Step[]>(initial?.steps ?? []);
  const [outputMode, setOutputMode] = useState(initial?.output_mode ?? "return");
  const [webhookUrl, setWebhookUrl] = useState(initial?.webhook_url ?? "");
  const [webhookHeaders, setWebhookHeaders] = useState(
    JSON.stringify(initial?.webhook_headers ?? {}, null, 2),
  );
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [saving, setSaving] = useState(false);

  const addModelStep = () =>
    setSteps([
      ...steps,
      {
        kind: "model",
        model_slug: models[0]?.slug ?? "",
        system: "",
        user_template: "{{input}}",
        vllm_overrides: {},
        response_format: "text",
      },
    ]);

  const addTransformStep = () =>
    setSteps([...steps, { kind: "transform", transform: "trim", params: {} }]);

  const updateStep = (idx: number, next: Step) =>
    setSteps(steps.map((s, i) => (i === idx ? next : s)));

  const moveStep = (idx: number, dir: -1 | 1) => {
    const target = idx + dir;
    if (target < 0 || target >= steps.length) return;
    const copy = [...steps];
    [copy[idx], copy[target]] = [copy[target], copy[idx]];
    setSteps(copy);
  };

  const deleteStep = (idx: number) =>
    setSteps(steps.filter((_, i) => i !== idx));

  const save = async () => {
    let parsedHeaders: Record<string, string> = {};
    try {
      parsedHeaders = JSON.parse(webhookHeaders || "{}");
    } catch (e) {
      toast.error(`Webhook headers JSON invalid: ${(e as Error).message}`);
      return;
    }
    if (steps.length === 0) {
      toast.error("Pipeline needs at least one step");
      return;
    }
    setSaving(true);
    try {
      await onSave({
        slug,
        label,
        steps,
        output_mode: outputMode,
        webhook_url: outputMode === "webhook" ? webhookUrl : null,
        webhook_headers: outputMode === "webhook" ? parsedHeaders : {},
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
        className="w-full max-w-3xl rounded-xl border border-zinc-800 bg-zinc-950 p-5 max-h-[92vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-4">{title}</h2>

        {/* Basics */}
        <div className="grid grid-cols-2 gap-3 mb-5">
          <F label="Slug" hint="url-safe, e.g. classify-then-reply">
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
        </div>

        {/* Steps */}
        <div className="mb-5">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs uppercase tracking-wider text-zinc-400">
              Steps
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={addModelStep}
                className="flex items-center gap-1.5 rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-xs hover:bg-zinc-800"
              >
                <Cpu className="h-3 w-3" /> + model
              </button>
              <button
                onClick={addTransformStep}
                className="flex items-center gap-1.5 rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-xs hover:bg-zinc-800"
              >
                <Wand2 className="h-3 w-3" /> + transform
              </button>
            </div>
          </div>
          {steps.length === 0 ? (
            <div className="rounded border border-dashed border-zinc-800 bg-zinc-950 p-6 text-center text-xs text-zinc-500">
              no steps yet — add one above
            </div>
          ) : (
            <ol className="space-y-2">
              {steps.map((step, idx) => (
                <li
                  key={idx}
                  className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3"
                >
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2 text-xs">
                      <span className="font-mono text-zinc-500">
                        {String(idx + 1).padStart(2, "0")}
                      </span>
                      <span
                        className={`rounded px-2 py-0.5 ring-1 ring-inset ${
                          step.kind === "model"
                            ? "bg-blue-950/60 text-blue-300 ring-blue-900"
                            : "bg-purple-950/60 text-purple-300 ring-purple-900"
                        }`}
                      >
                        {step.kind}
                      </span>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => moveStep(idx, -1)}
                        disabled={idx === 0}
                        className="p-1 rounded text-zinc-400 hover:text-zinc-100 disabled:opacity-30"
                      >
                        <ArrowUp className="h-3 w-3" />
                      </button>
                      <button
                        onClick={() => moveStep(idx, 1)}
                        disabled={idx === steps.length - 1}
                        className="p-1 rounded text-zinc-400 hover:text-zinc-100 disabled:opacity-30"
                      >
                        <ArrowDown className="h-3 w-3" />
                      </button>
                      <button
                        onClick={() => deleteStep(idx)}
                        className="p-1 rounded text-red-400 hover:text-red-300"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                  {step.kind === "model" ? (
                    <ModelStepEditor
                      step={step}
                      models={models}
                      onChange={(s) => updateStep(idx, s)}
                    />
                  ) : (
                    <TransformStepEditor
                      step={step}
                      onChange={(s) => updateStep(idx, s)}
                    />
                  )}
                </li>
              ))}
            </ol>
          )}
          <p className="mt-2 text-[11px] text-zinc-500">
            Templates in model steps can reference{" "}
            <code>{"{{input}}"}</code>, <code>{"{{prev}}"}</code>, and{" "}
            <code>{"{{step_N}}"}</code> (1-indexed).
          </p>
        </div>

        {/* Output */}
        <div className="mb-5 rounded-lg border border-zinc-800 p-3 space-y-3">
          <div className="text-xs uppercase tracking-wider text-zinc-400">
            Output
          </div>
          <F label="Mode">
            <select
              value={outputMode}
              onChange={(e) =>
                setOutputMode(
                  e.target.value as "return" | "webhook" | "json_only",
                )
              }
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
            >
              {OUTPUT_MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </F>
          {outputMode === "webhook" && (
            <>
              <F label="Webhook URL">
                <input
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  placeholder="https://your-app.example.com/hooks/llm"
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm font-mono"
                />
              </F>
              <F label="Webhook headers (JSON)">
                <textarea
                  value={webhookHeaders}
                  onChange={(e) => setWebhookHeaders(e.target.value)}
                  rows={3}
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
                />
              </F>
            </>
          )}
        </div>

        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="h-4 w-4 accent-zinc-200"
            />
            Enabled
          </label>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="rounded px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200"
            >
              Cancel
            </button>
            <button
              onClick={save}
              disabled={saving || !slug || !label}
              className="rounded bg-zinc-100 px-4 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ModelStepEditor({
  step,
  models,
  onChange,
}: {
  step: Extract<Step, { kind: "model" }>;
  models: Model[];
  onChange: (s: Step) => void;
}) {
  const [overridesText, setOverridesText] = useState(
    JSON.stringify(step.vllm_overrides ?? {}, null, 2),
  );
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-2">
        <F label="Model" className="col-span-2">
          <select
            value={step.model_slug}
            onChange={(e) => onChange({ ...step, model_slug: e.target.value })}
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
          >
            {models.map((m) => (
              <option key={m.id} value={m.slug}>
                {m.slug}
              </option>
            ))}
          </select>
        </F>
        <F label="Response format">
          <select
            value={step.response_format ?? "text"}
            onChange={(e) =>
              onChange({
                ...step,
                response_format: e.target.value as
                  | "text"
                  | "json_object"
                  | "json_schema",
              })
            }
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
          >
            <option value="text">text</option>
            <option value="json_object">json_object</option>
            <option value="json_schema">json_schema</option>
          </select>
        </F>
      </div>
      <F label="System">
        <textarea
          value={step.system ?? ""}
          onChange={(e) => onChange({ ...step, system: e.target.value })}
          rows={2}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
        />
      </F>
      <F label="User template">
        <textarea
          value={step.user_template ?? ""}
          onChange={(e) => onChange({ ...step, user_template: e.target.value })}
          rows={2}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
          placeholder="{{input}}"
        />
      </F>
      <F label="vLLM overrides (JSON)">
        <textarea
          value={overridesText}
          onChange={(e) => {
            setOverridesText(e.target.value);
            try {
              onChange({
                ...step,
                vllm_overrides: JSON.parse(e.target.value || "{}"),
              });
            } catch {
              // user still typing — don't update yet
            }
          }}
          rows={3}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
        />
      </F>
    </div>
  );
}

function TransformStepEditor({
  step,
  onChange,
}: {
  step: Extract<Step, { kind: "transform" }>;
  onChange: (s: Step) => void;
}) {
  const params = step.params ?? {};
  const setParam = (k: string, v: unknown) =>
    onChange({ ...step, params: { ...params, [k]: v } });
  return (
    <div className="space-y-2">
      <F label="Transform">
        <select
          value={step.transform}
          onChange={(e) => onChange({ ...step, transform: e.target.value, params: {} })}
          className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
        >
          {TRANSFORM_KINDS.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </F>
      {step.transform === "regex_replace" && (
        <div className="grid grid-cols-2 gap-2">
          <F label="Pattern">
            <input
              value={(params.pattern as string) ?? ""}
              onChange={(e) => setParam("pattern", e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
          </F>
          <F label="Replacement">
            <input
              value={(params.replacement as string) ?? ""}
              onChange={(e) => setParam("replacement", e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
          </F>
        </div>
      )}
      {step.transform === "replace" && (
        <div className="grid grid-cols-2 gap-2">
          <F label="Find">
            <input
              value={(params.find as string) ?? ""}
              onChange={(e) => setParam("find", e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
          </F>
          <F label="Replace with">
            <input
              value={(params.replace as string) ?? ""}
              onChange={(e) => setParam("replace", e.target.value)}
              className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
            />
          </F>
        </div>
      )}
      {(step.transform === "strip_prefix" ||
        step.transform === "strip_suffix") && (
        <F label={step.transform === "strip_prefix" ? "Prefix" : "Suffix"}>
          <input
            value={
              (params[
                step.transform === "strip_prefix" ? "prefix" : "suffix"
              ] as string) ?? ""
            }
            onChange={(e) =>
              setParam(
                step.transform === "strip_prefix" ? "prefix" : "suffix",
                e.target.value,
              )
            }
            className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-xs font-mono"
          />
        </F>
      )}
    </div>
  );
}

function F({
  label,
  hint,
  className,
  children,
}: {
  label: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={`block ${className ?? ""}`}>
      <span className="text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {children}
      {hint && <span className="text-[10px] text-zinc-500 mt-0.5 block">{hint}</span>}
    </label>
  );
}
