"use client";
import { useState } from "react";
import { Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { api, jsonBody } from "@/lib/api";
import { errorText, useRequireRole } from "@/lib/auth";
import { RuleChanger } from "@/components/RuleChanger";

interface PurgeReport { now: string; ttl_hours: number; dry_run: boolean; expired: string[]; deleted: string[]; kept: number; protected: number; errors: string[] }
interface AuditReport { intact: boolean; first_bad_seq: number | null; entries: number }

export default function AdminPage() {
  const { ready } = useRequireRole("admin");
  const [purge, setPurge] = useState<PurgeReport | null>(null);
  const [audit, setAudit] = useState<AuditReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run<T>(key: string, f: () => Promise<T>, set: (v: T) => void) {
    setBusy(key); setError(null);
    try { set(await f()); } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }
  if (!ready) return <p className="flex items-center gap-2 text-ink-soft"><Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading…</p>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Administration</h1>
      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      <RuleChanger />

      <section className="card space-y-3 p-4" aria-labelledby="purge-h">
        <h2 id="purge-h" className="flex items-center gap-2 font-semibold"><Trash2 aria-hidden className="h-5 w-5 text-brand" /> Delete expired drafts</h2>
        <p className="text-sm text-ink-soft">Uploads that were never submitted are deleted 48 hours after upload. Submitted applications are never touched. Run a preview first to see what would be deleted.</p>
        <div className="flex gap-2">
          <button className="btn-secondary" disabled={busy !== null} onClick={() => run("dry", () => api<PurgeReport>("/admin/purge", jsonBody({ dry_run: true })), setPurge)}>
            {busy === "dry" && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />} Preview
          </button>
          <button className="btn-danger" disabled={busy !== null} onClick={() => { if (window.confirm("Permanently delete all expired draft uploads?")) void run("real", () => api<PurgeReport>("/admin/purge", jsonBody({ dry_run: false })), setPurge); }}>
            {busy === "real" && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />} Delete expired drafts
          </button>
        </div>
        {purge && (
          <div role="status" className="rounded-lg bg-slate-50 p-3 text-sm">
            <p className="font-medium">{purge.dry_run ? "Preview: nothing was deleted." : "Done."} Limit {purge.ttl_hours} hours, at {purge.now}.</p>
            <ul className="mt-1 list-disc pl-5">
              <li>Expired: {purge.expired.length}{purge.dry_run ? " (would be deleted)" : ""}</li>
              {!purge.dry_run && <li>Deleted: {purge.deleted.length}</li>}
              <li>Kept (still within the limit): {purge.kept}</li>
              <li>Protected from deletion: {purge.protected}</li>
              {purge.errors.length > 0 && <li className="text-red-800">Errors: {purge.errors.join("; ")}</li>}
            </ul>
          </div>
        )}
      </section>

      <section className="card space-y-3 p-4" aria-labelledby="audit-h">
        <h2 id="audit-h" className="flex items-center gap-2 font-semibold"><ShieldCheck aria-hidden className="h-5 w-5 text-brand" /> Audit trail</h2>
        <p className="text-sm text-ink-soft">Every upload, assignment, decision and deletion is chained by hash. Verification detects any edited or removed entry.</p>
        <button className="btn-secondary" disabled={busy !== null} onClick={() => run("audit", () => api<AuditReport>("/admin/audit/verify"), setAudit)}>
          {busy === "audit" && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />} Verify audit trail
        </button>
        {audit && (
          <p role="status" className={`rounded-lg p-3 text-sm ${audit.intact ? "bg-emerald-50 text-emerald-900" : "bg-red-50 text-red-800"}`}>
            {audit.intact ? `Intact: ${audit.entries} entries verified.` : `Broken at entry ${audit.first_bad_seq}. The log has been altered.`}
          </p>
        )}
      </section>
    </div>
  );
}
