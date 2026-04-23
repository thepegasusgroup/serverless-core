const STYLES: Record<string, string> = {
  ready: "bg-green-950/60 text-green-300 ring-green-900",
  provisioning: "bg-yellow-950/60 text-yellow-300 ring-yellow-900",
  booting: "bg-yellow-950/60 text-yellow-300 ring-yellow-900",
  unhealthy: "bg-red-950/60 text-red-300 ring-red-900",
  destroyed: "bg-zinc-800 text-zinc-500 ring-zinc-700",
};

const DOT: Record<string, string> = {
  ready: "bg-green-400 animate-pulse",
  provisioning: "bg-yellow-400 animate-pulse",
  booting: "bg-yellow-400 animate-pulse",
  unhealthy: "bg-red-400",
  destroyed: "bg-zinc-500",
};

// Maps raw status to human-friendly stage label.
// Three-stage lifecycle: provisioning -> booting -> ready.
const STAGE_LABEL: Record<string, string> = {
  provisioning: "Stage 1/3 · Provisioning on vast.ai",
  booting: "Stage 2/3 · vLLM starting",
  ready: "Stage 3/3 · Ready",
  unhealthy: "Unhealthy",
  destroyed: "Destroyed",
};

export function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? "bg-zinc-800 text-zinc-400 ring-zinc-700";
  const dot = DOT[status] ?? "bg-zinc-500";
  const label = STAGE_LABEL[status] ?? status;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      {label}
    </span>
  );
}
