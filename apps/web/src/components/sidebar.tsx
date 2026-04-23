"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Server,
  Plus,
  Database,
  ScrollText,
  Settings,
  LogOut,
  Key,
  Terminal,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";

const groups: {
  label: string;
  items: {
    href: string;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    disabled?: boolean;
  }[];
}[] = [
  {
    label: "Manage",
    items: [
      { href: "/instances", label: "Instances", icon: Server },
      { href: "/instances/new", label: "Rent", icon: Plus },
      { href: "/run", label: "Run", icon: Terminal },
    ],
  },
  {
    label: "Config",
    items: [
      { href: "/keys", label: "API keys", icon: Key },
      { href: "/logs", label: "Logs", icon: ScrollText },
      { href: "/models", label: "Models", icon: Database, disabled: true },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getUser().then(({ data: { user } }) => {
      setEmail(user?.email ?? null);
    });
  }, []);

  const signOut = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    window.location.href = "/login";
  };

  return (
    <aside className="fixed left-0 top-0 z-10 flex h-screen w-60 flex-col border-r border-zinc-800 bg-zinc-950">
      {/* brand */}
      <div className="px-5 py-5 border-b border-zinc-900">
        <Link href="/instances" className="block">
          <div className="text-base font-semibold text-zinc-100 tracking-tight">
            serverless-core
          </div>
          <div className="text-[11px] text-zinc-500 mt-0.5">
            vast.ai control plane
          </div>
        </Link>
      </div>

      {/* nav groups */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-6">
        {groups.map((group) => (
          <div key={group.label}>
            <div className="px-2 mb-2 text-[10px] uppercase tracking-wider text-zinc-500 font-medium">
              {group.label}
            </div>
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const active =
                  item.href === "/instances"
                    ? pathname === "/instances"
                    : pathname.startsWith(item.href);
                const Icon = item.icon;

                if (item.disabled) {
                  return (
                    <div
                      key={item.href}
                      className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm text-zinc-600 cursor-not-allowed"
                    >
                      <Icon className="h-4 w-4" />
                      <span>{item.label}</span>
                      <span className="ml-auto text-[10px] text-zinc-600">
                        soon
                      </span>
                    </div>
                  );
                }
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm transition-colors ${
                      active
                        ? "bg-zinc-800 text-zinc-100"
                        : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100"
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* footer */}
      <div className="border-t border-zinc-900 p-3">
        {email && (
          <div className="px-2 mb-2 text-[11px] text-zinc-500 truncate">
            {email}
          </div>
        )}
        <div className="flex items-center gap-1">
          <button
            className="flex items-center gap-2 flex-1 px-2.5 py-1.5 rounded-md text-sm text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200 cursor-not-allowed"
            disabled
            title="Settings (coming soon)"
          >
            <Settings className="h-4 w-4" />
            <span>Settings</span>
          </button>
          <button
            onClick={signOut}
            className="p-1.5 rounded-md text-zinc-500 hover:bg-zinc-900 hover:text-red-400"
            title="Sign out"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </aside>
  );
}
