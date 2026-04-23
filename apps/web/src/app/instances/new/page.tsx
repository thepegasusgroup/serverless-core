"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

type Offer = {
  id: number;
  gpu_name: string;
  gpu_ram_gb: number;
  dph: number;
  reliability: number;
  cpu_cores_effective: number | null;
  cpu_ghz: number | null;
  inet_down_mbps: number | null;
  datacenter: string | null;
};

export default function NewInstancePage() {
  const router = useRouter();
  const [gpu, setGpu] = useState("RTX_5090");
  const [region, setRegion] = useState("eu");
  const [maxDph, setMaxDph] = useState(0.3);
  const [minCpu, setMinCpu] = useState(12);
  const [minBandwidth, setMinBandwidth] = useState(500);
  const [datacenterOnly, setDatacenterOnly] = useState(true);
  const [modelSlug, setModelSlug] = useState("qwen2.5-7b-instruct");

  const [offers, setOffers] = useState<Offer[]>([]);
  const [searching, setSearching] = useState(false);
  const [renting, setRenting] = useState<number | null>(null);

  const search = async () => {
    setSearching(true);
    try {
      const params = new URLSearchParams({
        gpu,
        region,
        max_dph: String(maxDph),
        min_cpu_cores: String(minCpu),
        min_bandwidth: String(minBandwidth),
        datacenter_only: String(datacenterOnly),
        limit: "20",
      });
      const data = await api<Offer[]>(`/admin/offers?${params}`);
      setOffers(data);
      if (data.length === 0) toast.info("No offers matched those filters.");
    } catch (e) {
      toast.error(`Search failed: ${(e as Error).message}`);
    } finally {
      setSearching(false);
    }
  };

  const rent = async (offerId: number) => {
    if (!confirm(`Rent offer ${offerId} for ${modelSlug}?`)) return;
    setRenting(offerId);
    try {
      await api("/admin/instances/rent", {
        method: "POST",
        body: JSON.stringify({ offer_id: offerId, model_slug: modelSlug }),
      });
      toast.success("Rented — redirecting…");
      router.push("/instances");
    } catch (e) {
      toast.error(`Rent failed: ${(e as Error).message}`);
      setRenting(null);
    }
  };

  return (
    <AppShell>
      <main className="min-h-screen p-8 max-w-6xl mx-auto">
        <header className="mb-6">
          <h1 className="text-3xl font-semibold">Rent a new instance</h1>
          <p className="text-sm text-zinc-500 mt-1">
            Filter vast.ai offers, then click rent.
          </p>
        </header>

        <section className="mb-8 rounded-xl border border-zinc-800 bg-zinc-950 p-6">
          <div className="grid grid-cols-2 md:grid-cols-6 gap-4 mb-4">
            <Field label="GPU" value={gpu} onChange={setGpu} />
            <Field
              label="Region"
              value={region}
              onChange={setRegion}
              help="eu / us / na"
            />
            <Field
              label="Max $/hr"
              value={String(maxDph)}
              onChange={(v) => setMaxDph(parseFloat(v) || 0)}
            />
            <Field
              label="Min CPU"
              value={String(minCpu)}
              onChange={(v) => setMinCpu(parseInt(v) || 0)}
            />
            <Field
              label="Min Mbps"
              value={String(minBandwidth)}
              onChange={(v) => setMinBandwidth(parseInt(v) || 0)}
            />
            <Field label="Model" value={modelSlug} onChange={setModelSlug} />
          </div>
          <div className="flex items-center gap-4 mb-4">
            <label className="flex items-center gap-2 text-sm text-zinc-300 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={datacenterOnly}
                onChange={(e) => setDatacenterOnly(e.target.checked)}
                className="h-4 w-4 rounded border-zinc-600 bg-zinc-900 accent-zinc-200"
              />
              Datacenter hosts only
              <span className="text-[11px] text-zinc-500">
                (pricier but reliable)
              </span>
            </label>
          </div>
          <button
            onClick={search}
            disabled={searching}
            className="rounded bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-60"
          >
            {searching ? "Searching…" : "Search offers"}
          </button>
        </section>

        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900/60 text-xs uppercase tracking-wider text-zinc-500">
              <tr>
                <th className="px-4 py-3 text-left font-medium">id</th>
                <th className="px-4 py-3 text-left font-medium">gpu</th>
                <th className="px-4 py-3 text-right font-medium">vram</th>
                <th className="px-4 py-3 text-right font-medium">$/hr</th>
                <th className="px-4 py-3 text-right font-medium">rel</th>
                <th className="px-4 py-3 text-right font-medium">cpu</th>
                <th className="px-4 py-3 text-right font-medium">down</th>
                <th className="px-4 py-3 text-left font-medium">dc</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {offers.length === 0 ? (
                <tr>
                  <td
                    colSpan={9}
                    className="px-4 py-12 text-center text-zinc-500"
                  >
                    no results — adjust filters and search
                  </td>
                </tr>
              ) : (
                offers.map((o) => (
                  <tr
                    key={o.id}
                    className="border-t border-zinc-800 hover:bg-zinc-900/30 transition-colors"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-zinc-300">
                      {o.id}
                    </td>
                    <td className="px-4 py-3">{o.gpu_name}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {o.gpu_ram_gb}GB
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      ${o.dph.toFixed(3)}
                    </td>
                    <td className="px-4 py-3 text-right text-xs text-zinc-400 tabular-nums">
                      {(o.reliability * 100).toFixed(1)}%
                    </td>
                    <td className="px-4 py-3 text-right text-xs tabular-nums">
                      {o.cpu_cores_effective ?? "-"}
                    </td>
                    <td className="px-4 py-3 text-right text-xs tabular-nums">
                      {o.inet_down_mbps?.toFixed(0) ?? "-"}
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-400">
                      {o.datacenter ?? "-"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => rent(o.id)}
                        disabled={renting !== null}
                        className="rounded border border-green-900 bg-green-950/40 px-2.5 py-1 text-xs font-medium text-green-300 hover:bg-green-950/70 hover:text-green-200 disabled:opacity-60"
                      >
                        {renting === o.id ? "…" : "rent"}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </main>
    </AppShell>
  );
}

function Field({
  label,
  value,
  onChange,
  help,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  help?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus:border-zinc-500 focus:outline-none"
      />
      {help && <span className="mt-1 text-[10px] text-zinc-600">{help}</span>}
    </label>
  );
}
