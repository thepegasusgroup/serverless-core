"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";

const links = [
  { href: "/instances", label: "Instances" },
  { href: "/instances/new", label: "Rent" },
];

export function Nav() {
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
    <nav className="border-b border-zinc-800 bg-zinc-950/80 backdrop-blur sticky top-0 z-10">
      <div className="max-w-6xl mx-auto px-8 py-3 flex items-center justify-between">
        <div className="flex items-center gap-8">
          <Link href="/instances" className="font-semibold text-zinc-100">
            serverless-core
          </Link>
          <div className="flex items-center gap-1 text-sm">
            {links.map((l) => {
              const active =
                l.href === "/instances"
                  ? pathname === "/instances"
                  : pathname.startsWith(l.href);
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  className={`rounded px-2.5 py-1 transition-colors ${
                    active
                      ? "bg-zinc-800 text-zinc-100"
                      : "text-zinc-400 hover:text-zinc-100"
                  }`}
                >
                  {l.label}
                </Link>
              );
            })}
          </div>
        </div>
        <div className="flex items-center gap-4 text-sm">
          {email && <span className="text-zinc-500">{email}</span>}
          <button
            onClick={signOut}
            className="text-zinc-500 hover:text-zinc-200"
          >
            sign out
          </button>
        </div>
      </div>
    </nav>
  );
}
