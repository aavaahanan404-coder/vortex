"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Loader2, UserPlus } from "lucide-react";
import { api, type MyCase, type QueueRow } from "@/lib/api";
import { errorText, useRequireRole } from "@/lib/auth";

const CATEGORY: Record<string, { label: string; tone: string }> = {
  high_anomaly: { label: "High anomaly", tone: "bg-red-100 text-red-800" },
  needs_human: { label: "Needs a person", tone: "bg-amber-100 text-amber-900" },
  applicant_fixable: { label: "Applicant can fix", tone: "bg-sky-100 text-sky-900" },
  auto_clear_candidate: { label: "Auto-clear candidate", tone: "bg-emerald-100 text-emerald-900" },
};

export default function ReviewHome() {
  const { me, ready } = useRequireRole("reviewer", "supervisor");
  if (!ready || !me) return <p className="flex items-center gap-2 text-ink-soft"><Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading…</p>;
  return me.role === "supervisor" ? <Queue /> : <MyCases />;
}

function Queue() {
  const [rows, setRows] = useState<QueueRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => api<QueueRow[]>("/review/queue").then(setRows).catch((e) => setError(errorText(e))), []);
  useEffect(() => { void load(); }, [load]);

  async function assign(inst: string) {
    setBusy(inst); setError(null);
    try { await api(`/review/cases/${inst}/assign`, { method: "POST" }); await load(); } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Review queue</h1>
        <p className="text-sm text-ink-soft">Ordered by anomaly score. The score is shown to supervisors only; reviewers decide blind and see it after they have decided.</p>
      </div>
      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {!rows ? <p className="flex items-center gap-2 text-ink-soft"><Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading…</p>
        : rows.length === 0 ? <p className="card p-6 text-center text-ink-soft">No submitted applications yet.</p>
        : (
          <div className="card overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Submitted applications by anomaly score</caption>
              <thead className="border-b border-slate-200 text-ink-faint"><tr>
                <th scope="col" className="p-3">Institution</th><th scope="col" className="p-3">Score</th><th scope="col" className="p-3">Category</th>
                <th scope="col" className="p-3">Review</th><th scope="col" className="p-3">Reviewers</th><th scope="col" className="p-3"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {rows.map((r) => {
                  const c = CATEGORY[r.category] ?? { label: r.category, tone: "bg-slate-100" };
                  return (
                    <tr key={r.inst_id} className="border-b border-slate-100 last:border-0">
                      <th scope="row" className="p-3 font-medium"><Link className="text-brand underline" href={`/review/${r.inst_id}`}>{r.inst_id}</Link><div className="font-normal text-ink-faint">{r.name}</div></th>
                      <td className="p-3 tabular-nums">{r.score.toFixed(2)}</td>
                      <td className="p-3"><span className={`pill ${c.tone}`}>{c.label}</span></td>
                      <td className="p-3">{r.phase.replace(/_/g, " ")}{r.double_review && <span className="pill ml-2 bg-slate-100 text-ink-soft">double</span>}</td>
                      <td className="p-3">{r.assigned.length ? r.assigned.join(", ") : <span className="text-ink-faint">none</span>}</td>
                      <td className="p-3 text-right">
                        {r.phase === "unassigned" && (
                          <button className="btn-secondary" disabled={busy === r.inst_id} onClick={() => assign(r.inst_id)}>
                            {busy === r.inst_id ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <UserPlus aria-hidden className="h-4 w-4" />} Assign
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}

function MyCases() {
  const [rows, setRows] = useState<MyCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<MyCase[]>("/review/my-cases").then(setRows).catch((e) => setError(errorText(e))); }, []);
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">My cases</h1>
        <p className="text-sm text-ink-soft">You review blind: you see the documents and the evidence, not the machine&apos;s score, until you have recorded your decision.</p>
      </div>
      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {!rows ? <p className="flex items-center gap-2 text-ink-soft"><Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading…</p>
        : rows.length === 0 ? <p className="card p-6 text-center text-ink-soft">Nothing is assigned to you.</p>
        : (
          <ul className="grid gap-3 md:grid-cols-2">
            {rows.map((r) => (
              <li key={r.inst_id} className="card flex items-center justify-between p-4">
                <div><Link href={`/review/${r.inst_id}`} className="font-medium text-brand underline">{r.inst_id}</Link>
                  <p className="text-sm text-ink-soft">{r.you_submitted ? "You have decided" : "Waiting for your decision"} · {r.phase.replace(/_/g, " ")}</p></div>
                <span className={`pill ${r.you_submitted ? "bg-emerald-100 text-emerald-900" : "bg-amber-100 text-amber-900"}`}>{r.you_submitted ? "done" : "to do"}</span>
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}
