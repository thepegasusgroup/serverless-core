"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Copy, Check } from "lucide-react";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

type ApiKey = {
  id: string;
  label: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

type CreateResp = {
  id: string;
  label: string;
  prefix: string;
  key: string;
  created_at: string;
};

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [newLabel, setNewLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [revealedKey, setRevealedKey] = useState<CreateResp | null>(null);
  const [copied, setCopied] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const rows = await api<ApiKey[]>("/admin/api-keys");
      setKeys(rows);
    } catch (e) {
      toast.error(`Load failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const create = async () => {
    const label = newLabel.trim();
    if (!label) return;
    setCreating(true);
    try {
      const resp = await api<CreateResp>("/admin/api-keys", {
        method: "POST",
        body: JSON.stringify({ label }),
      });
      setRevealedKey(resp);
      setNewLabel("");
      toast.success("Key created. Copy it now — it won't be shown again.");
      await fetchAll();
    } catch (e) {
      toast.error(`Create failed: ${(e as Error).message}`);
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (id: string, label: string) => {
    if (!confirm(`Revoke "${label}"? Cannot be undone.`)) return;
    try {
      await api(`/admin/api-keys/${id}`, { method: "DELETE" });
      toast.success("Revoked");
      await fetchAll();
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
        <header className="mb-6">
          <h1 className="text-3xl font-semibold">API keys</h1>
          <p className="text-sm text-zinc-500 mt-1">
            For clients hitting <code className="text-zinc-300">/v1/chat/completions</code>
          </p>
        </header>

        {/* Reveal box — shown ONCE after creation */}
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

        {/* Create */}
        <section className="mb-8 rounded-xl border border-zinc-800 bg-zinc-950 p-6">
          <div className="flex items-end gap-3">
            <label className="flex-1">
              <span className="text-xs uppercase tracking-wider text-zinc-500">
                New key label
              </span>
              <input
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
                placeholder="e.g. production-web"
                className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none"
                onKeyDown={(e) => e.key === "Enter" && create()}
              />
            </label>
            <button
              onClick={create}
              disabled={creating || !newLabel.trim()}
              className="rounded bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
            >
              {creating ? "Creating…" : "Create key"}
            </button>
          </div>
        </section>

        {/* List */}
        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3 text-left font-medium">label</th>
                <th className="px-4 py-3 text-left font-medium">prefix</th>
                <th className="px-4 py-3 text-left font-medium">created</th>
                <th className="px-4 py-3 text-left font-medium">last used</th>
                <th className="px-4 py-3 text-left font-medium">status</th>
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
              ) : keys.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-zinc-500">
                    no keys yet — create one above
                  </td>
                </tr>
              ) : (
                keys.map((k) => {
                  const revoked = !!k.revoked_at;
                  return (
                    <tr
                      key={k.id}
                      className="border-t border-zinc-800 hover:bg-zinc-900/30"
                    >
                      <td className="px-4 py-3">{k.label}</td>
                      <td className="px-4 py-3 font-mono text-xs text-zinc-400">
                        {k.prefix}…
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-400">
                        {new Date(k.created_at).toLocaleString()}
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
                      <td className="px-4 py-3 text-right">
                        {!revoked && (
                          <button
                            onClick={() => revoke(k.id, k.label)}
                            className="rounded border border-red-900 bg-red-950/40 px-2.5 py-1 text-xs font-medium text-red-300 hover:bg-red-950/70"
                          >
                            revoke
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
