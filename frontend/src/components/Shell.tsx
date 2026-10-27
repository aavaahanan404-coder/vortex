"use client";
import type { ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FileCheck2, LogOut } from "lucide-react";
import { useAuth, homeFor } from "@/lib/auth";

const ROLE_LABEL = { applicant: "Applicant", reviewer: "Reviewer", supervisor: "Supervisor", admin: "Administrator" } as const;

export function Shell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  const router = useRouter();
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <Link href={me ? homeFor(me.role) : "/"} className="flex items-center gap-2 font-semibold text-brand">
            <FileCheck2 aria-hidden className="h-5 w-5" /> Document verification portal
          </Link>
          {me && (
            <div className="flex items-center gap-3 text-sm">
              <span className="text-ink-soft">
                {ROLE_LABEL[me.role]}{me.inst_id ? ` · ${me.inst_id}` : me.user ? ` · ${me.user}` : ""}
              </span>
              <button className="btn-secondary !px-3 !py-1.5" onClick={() => { logout(); router.replace("/"); }}>
                <LogOut aria-hidden className="h-4 w-4" /> Sign out
              </button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}
