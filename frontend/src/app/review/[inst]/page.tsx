"use client";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Download, EyeOff, Gavel, GitCompare, Loader2, ShieldAlert } from "lucide-react";
import { api, ApiError, blob, DOC_LABEL, jsonBody, type CaseDoc, type Finding, type PageSize, type ReviewerCase, type SupervisorCase } from "@/lib/api";
import { errorText, useRequireRole } from "@/lib/auth";
import { PageViewer, type Box } from "@/components/PageViewer";

interface DiffReport {
  pages_old: number; pages_new: number; changed_pages: number[]; n_text_changes: number; n_visual_regions: number;
  text_changes: { page: number; op: string; old: string; new: string; old_bbox: number[] | null; new_bbox: number[] | null }[];
  visual_regions: { page: number; bbox: number[] }[];
}

export default function CasePage({ params }: { params: Promise<{ inst: string }> }) {
  const { inst } = use(params);
  const { me, ready } = useRequireRole("reviewer", "supervisor");
  const [rc, setRc] = useState<ReviewerCase | null>(null);
  const [sc, setSc] = useState<SupervisorCase | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api<ReviewerCase | SupervisorCase>(`/review/cases/${inst}`);
      if ("result" in d) setSc(d); else setRc(d);
    } catch (e) { setError(e instanceof ApiError && e.status === 403 ? "This case is not assigned to you." : errorText(e)); }
  }, [inst]);
  useEffect(() => { if (ready) void load(); }, [ready, load]);

  if (error) return <div className="space-y-3"><BackLink /><p role="alert" className="rounded-lg bg-red-50 p-3 text-red-800">{error}</p></div>;
  if (!ready || !me || (!rc && !sc)) return <p className="flex items-center gap-2 text-ink-soft"><Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading…</p>;

  // Both roles share the document viewer; what differs is the machine information shown and whether a decision can be recorded.
  const documents: CaseDoc[] = rc ? rc.documents : sc!.result.documents.map((d) => ({ ...d, pages: sc!.pages[d.doc_type] ?? [] }));
  const findings: Finding[] = rc ? (rc.findings_with_scores ?? rc.findings) : sc!.result.findings.filter((f) => f.kind !== "info");
  const blind = !!rc && rc.score === null;
  const title = rc ? rc.application.name ?? inst : sc!.result.app.name ?? inst;

  return (
    <div className="space-y-5">
      <BackLink />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{title} <span className="text-ink-faint">· {inst}</span></h1>
          <p className="text-sm text-ink-soft">Application date {rc ? rc.application.application_date : sc!.result.app.application_date}</p>
        </div>
        <EvidenceButton inst={inst} />
      </div>

      {blind && (
        <p role="status" className="flex gap-2 rounded-lg bg-brand-light p-3 text-sm text-brand-dark">
          <EyeOff aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
          Blind review. The machine&apos;s score and confidence are hidden until you have recorded your decision, so that your judgement is your own. The findings below are evidence to check, not verdicts.
        </p>
      )}
      {sc && <SupervisorSummary sc={sc} />}
      {rc && !blind && rc.score !== null && <RevealedSummary rc={rc} />}

      <Viewer inst={inst} documents={documents} findings={findings} />

      {rc && <DecisionPanel inst={inst} rc={rc} onDone={load} />}
      {sc && <SupervisorRules sc={sc} />}
    </div>
  );
}

function BackLink() {
  return <Link href="/review" className="inline-flex items-center gap-1 text-sm text-brand underline"><ArrowLeft aria-hidden className="h-4 w-4" /> Back to cases</Link>;
}

function EvidenceButton({ inst }: { inst: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function get() {
    setBusy(true); setErr(null);
    try {
      const b = await blob(`/review/cases/${inst}/evidence.pdf`);
      const u = URL.createObjectURL(b), a = document.createElement("a");
      a.href = u; a.download = `${inst}-evidence.pdf`; a.click(); setTimeout(() => URL.revokeObjectURL(u), 10_000);
    } catch { setErr("The evidence report could not be created."); } finally { setBusy(false); }
  }
  return (
    <div className="text-right">
      <button className="btn-secondary" onClick={get} disabled={busy}>{busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Download aria-hidden className="h-4 w-4" />} Evidence report (PDF)</button>
      {err && <p role="alert" className="mt-1 text-xs text-red-700">{err}</p>}
    </div>
  );
}

const CAT: Record<string, string> = { high_anomaly: "High anomaly", needs_human: "Needs a person", applicant_fixable: "Applicant can fix", auto_clear_candidate: "Auto-clear candidate" };

function Reasons({ reasons }: { reasons: { finding_id: string; message: string; contribution: number }[] }) {
  return (
    <ol className="mt-2 list-decimal space-y-1 pl-6 text-sm">
      {reasons.filter((r) => r.contribution > 0.005).map((r, i) => <li key={`${r.finding_id}${i}`}>{r.message} <span className="text-ink-faint">(contributes {r.contribution.toFixed(2)})</span></li>)}
    </ol>
  );
}

function SupervisorSummary({ sc }: { sc: SupervisorCase }) {
  const s = sc.result.score;
  return (
    <section className="card p-4" aria-label="Machine assessment">
      <h2 className="flex items-center gap-2 font-semibold"><ShieldAlert aria-hidden className="h-5 w-5 text-brand" /> Machine assessment</h2>
      <p className="mt-1 text-sm">Score <strong className="tabular-nums">{s.score.toFixed(2)}</strong> · {CAT[s.category] ?? s.category} · {sc.review.double ? "double review" : "single review"} · review {sc.review.phase.replace(/_/g, " ")}
        {sc.review.outcome && <> · outcome <strong>{sc.review.outcome.replace("_", " ")}</strong></>}</p>
      <Reasons reasons={s.reasons} />
      {Object.keys(sc.review.decisions).length > 0 && (
        <p className="mt-2 text-sm text-ink-soft">Decisions: {Object.entries(sc.review.decisions).map(([u, d]) => `${u}: ${d.replace("_", " ")}`).join(" · ")}</p>
      )}
      <p className="mt-2 text-xs text-ink-faint">The score ranks the queue; it never approves or rejects an application.</p>
    </section>
  );
}

function RevealedSummary({ rc }: { rc: ReviewerCase }) {
  return (
    <section className="card p-4" aria-label="Machine assessment">
      <h2 className="font-semibold">Machine assessment (now visible)</h2>
      <p className="mt-1 text-sm">Score <strong className="tabular-nums">{rc.score!.toFixed(2)}</strong> · {CAT[rc.category ?? ""] ?? rc.category}{rc.outcome && <> · outcome <strong>{rc.outcome.replace("_", " ")}</strong></>}</p>
      {rc.reasons && <Reasons reasons={rc.reasons} />}
    </section>
  );
}

function SupervisorRules({ sc }: { sc: SupervisorCase }) {
  const rules = sc.result.rules.filter((r) => r.result === "fail" || r.result === "not_evaluable");
  if (!rules.length) return null;
  return (
    <section className="card p-4" aria-label="Rules">
      <h2 className="font-semibold">Rules needing attention</h2>
      <ul className="mt-2 space-y-1 text-sm">
        {rules.map((r) => <li key={r.id}><span className={`pill mr-2 ${r.result === "fail" ? "bg-red-100 text-red-800" : "bg-slate-100 text-ink-soft"}`}>{r.result.replace("_", " ")}</span><strong>{r.id}</strong> {r.name}: {r.detail}</li>)}
      </ul>
    </section>
  );
}

function boxOf(f: Finding): Box | null {
  const b = f.bbox;
  if (!b || b.length < 5) return null;
  return { page: b[0], x: b[1], y: b[2], w: b[3], h: b[4], id: `${f.id}:${f.doc_id}:${b.join(",")}` };
}

function Viewer({ inst, documents, findings }: { inst: string; documents: CaseDoc[]; findings: Finding[] }) {
  const [docType, setDocType] = useState(documents[0]?.doc_type ?? "");
  const [page, setPage] = useState(1);
  const [active, setActive] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const doc = documents.find((d) => d.doc_type === docType);
  const sizes: PageSize[] = doc?.pages ?? [];
  const size = sizes[page - 1] ?? { w: 595, h: 842 };

  const docFindings = useMemo(() => findings.filter((f) => f.doc_id === doc?.doc_id), [findings, doc]);
  const caseWide = useMemo(() => findings.filter((f) => !documents.some((d) => d.doc_id === f.doc_id)), [findings, documents]);
  const boxes = useMemo(() => docFindings.map(boxOf).filter((b): b is Box => !!b).map((b) => ({ ...b, active: b.id === active })), [docFindings, active]);

  const [diff, setDiff] = useState<DiffReport | null | "none">(null);
  useEffect(() => {
    setDiff(null); setShowDiff(false); setPage(1); setActive(null);
    if (!docType) return;
    api<DiffReport>(`/review/cases/${inst}/documents/${docType}/diff`).then(setDiff).catch(() => setDiff("none"));
  }, [inst, docType]);
  const diffBoxes: Box[] = showDiff && diff && diff !== "none"
    ? [...diff.visual_regions.map((r, i) => ({ page: r.page, x: r.bbox[0], y: r.bbox[1], w: r.bbox[2], h: r.bbox[3], id: `v${i}`, tone: "diff-new" as const })),
       ...diff.text_changes.flatMap((t, i) => t.old_bbox ? [{ page: t.page, x: t.old_bbox[0], y: t.old_bbox[1], w: t.old_bbox[2], h: t.old_bbox[3], id: `t${i}`, tone: "diff-old" as const }] : [])]
    : [];

  return (
    <section aria-label="Documents" className="space-y-3">
      <div role="tablist" aria-label="Documents" className="flex flex-wrap gap-1">
        {documents.map((d) => (
          <button key={d.doc_type} role="tab" aria-selected={d.doc_type === docType} onClick={() => setDocType(d.doc_type)}
                  className={`rounded-lg px-3 py-1.5 text-sm ${d.doc_type === docType ? "bg-brand text-white" : "bg-white text-ink ring-1 ring-slate-300 hover:bg-slate-50"}`}>
            {DOC_LABEL[d.doc_type] ?? d.doc_type}
            {findings.some((f) => f.doc_id === d.doc_id) && <span className="ml-1.5 inline-block h-2 w-2 rounded-full bg-red-500" aria-label="has findings" />}
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-2">
          {sizes.length > 1 && (
            <div className="flex items-center gap-2 text-sm">
              {sizes.map((_, i) => <button key={i} className={`rounded px-2 py-0.5 ${page === i + 1 ? "bg-brand text-white" : "ring-1 ring-slate-300"}`} onClick={() => setPage(i + 1)} aria-label={`Page ${i + 1}`} aria-current={page === i + 1}>{i + 1}</button>)}
            </div>
          )}
          {sizes.length === 0
            ? <p className="card p-4 text-sm text-ink-soft">This document is an image upload and cannot be shown page by page. Open the evidence report for it.</p>
            : <PageViewer inst={inst} docType={docType} page={page} size={size} boxes={[...boxes, ...diffBoxes]} onBox={setActive} />}
        </div>

        <div className="space-y-4">
          <div className="card p-4">
            <h3 className="font-semibold">Findings on this document</h3>
            {docFindings.length === 0 ? <p className="mt-1 text-sm text-ink-soft">None.</p> : (
              <ul className="mt-2 space-y-2">
                {docFindings.map((f, i) => {
                  const b = boxOf(f);
                  return (
                    <li key={`${f.id}${i}`}>
                      <button className={`w-full rounded-lg border p-3 text-left text-sm ${b && b.id === active ? "border-red-500 bg-red-50" : "border-slate-200 hover:bg-slate-50"}`}
                              onClick={() => { if (b) { setPage(b.page); setActive(b.id); } }} aria-label={`Finding ${f.id}${b ? ", show on page" : ""}`}>
                        <span className="pill mr-2 bg-slate-100 text-ink-soft">tier {f.tier}</span><span className="font-medium">{f.id}</span>
                        {typeof f.p === "number" && <span className="ml-2 text-ink-faint">p={f.p.toFixed(2)}{f.kind ? ` · ${f.kind}` : ""}</span>}
                        <span className="mt-1 block">{f.message}</span>
                        {f.tier === 4 && <span className="mt-1 block text-xs text-amber-800">Image-based signal: a pointer for you to look at, never proof by itself.</span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {caseWide.length > 0 && (
            <div className="card p-4">
              <h3 className="font-semibold">Findings across documents</h3>
              <ul className="mt-2 space-y-2 text-sm">{caseWide.map((f, i) => <li key={`${f.id}${i}`}><span className="pill mr-2 bg-slate-100 text-ink-soft">tier {f.tier}</span><strong>{f.id}</strong> {f.message}</li>)}</ul>
            </div>
          )}

          {diff && diff !== "none" && (
            <div className="card p-4">
              <h3 className="flex items-center gap-2 font-semibold"><GitCompare aria-hidden className="h-4 w-4" /> Changes since the earlier version</h3>
              <p className="mt-1 text-sm text-ink-soft">{diff.changed_pages.length ? `${diff.n_text_changes} text change(s), ${diff.n_visual_regions} visual region(s) on page(s) ${diff.changed_pages.join(", ")}.` : "No differences found in the compared pages."}</p>
              <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={showDiff} onChange={(e) => setShowDiff(e.target.checked)} /> Mark changes on the page (orange: earlier text, green: changed areas)</label>
              {diff.text_changes.length > 0 && (
                <table className="mt-2 w-full text-left text-sm">
                  <caption className="sr-only">Text changes</caption>
                  <thead className="text-ink-faint"><tr><th scope="col" className="py-1">Page</th><th scope="col">Was</th><th scope="col">Now</th></tr></thead>
                  <tbody>{diff.text_changes.slice(0, 20).map((t, i) => <tr key={i} className="border-t border-slate-100"><td className="py-1">{t.page}</td><td className="pr-2 text-red-800">{t.old || "—"}</td><td className="text-emerald-800">{t.new || "—"}</td></tr>)}</tbody>
                </table>
              )}
            </div>
          )}

          {doc && Object.keys(doc.fields).length > 0 && (
            <details className="card p-4 text-sm">
              <summary className="cursor-pointer font-semibold">Extracted fields ({Object.keys(doc.fields).length})</summary>
              <table className="mt-2 w-full text-left">
                <caption className="sr-only">Fields read from the document</caption>
                <tbody>
                  {Object.entries(doc.fields).slice(0, 60).map(([k, v]) => (
                    <tr key={k} className="border-t border-slate-100">
                      <th scope="row" className="w-2/5 py-1 pr-2 align-top font-normal text-ink-faint">{k.replace(/[._]/g, " ")}</th>
                      <td className="py-1">{v.value}{v.alt_values && v.alt_values.length > 0 && <span className="ml-1 pill bg-amber-100 text-amber-900">also: {v.alt_values.join(", ")}</span>}
                        {v.flags && v.flags.length > 0 && <span className="ml-1 pill bg-slate-100 text-ink-soft">{v.flags.join(", ")}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      </div>
    </section>
  );
}

const CHOICES: { value: string; label: string; hint: string }[] = [
  { value: "approve", label: "Approve", hint: "The documents are genuine and consistent." },
  { value: "request_info", label: "Request information", hint: "Something is unclear; ask the applicant." },
  { value: "reject", label: "Reject", hint: "The documents are forged or the application fails the norms." },
];

function DecisionPanel({ inst, rc, onDone }: { inst: string; rc: ReviewerCase; onDone: () => Promise<void> }) {
  const [choice, setChoice] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const decided = rc.your_decision !== null;
  const open = !decided && (rc.phase === "blind" || rc.phase === "awaiting_third");

  async function send() {
    setBusy(true); setError(null);
    try { await api(`/review/cases/${inst}/decision`, jsonBody({ decision: choice, notes })); await onDone(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }

  if (decided) {
    return (
      <section className="card p-4" aria-label="Your decision">
        <h2 className="flex items-center gap-2 font-semibold"><Gavel aria-hidden className="h-5 w-5 text-brand" /> Your decision</h2>
        <p className="mt-1 text-sm">You recorded <strong>{rc.your_decision!.replace("_", " ")}</strong>. {rc.phase === "resolved" ? `The case is resolved: ${rc.outcome?.replace("_", " ")}.` : rc.phase === "committee" ? "The reviewers disagreed three ways; a committee decides." : "Waiting for the other reviewer."}</p>
        {rc.your_notes && <p className="mt-1 text-sm text-ink-soft">Notes: {rc.your_notes}</p>}
      </section>
    );
  }
  if (!open) return <p className="card p-4 text-sm text-ink-soft">This case is {rc.phase.replace(/_/g, " ")}; no decision can be recorded.</p>;
  return (
    <section className="card p-4" aria-label="Record your decision">
      <h2 className="flex items-center gap-2 font-semibold"><Gavel aria-hidden className="h-5 w-5 text-brand" /> Record your decision</h2>
      <fieldset className="mt-3 space-y-2">
        <legend className="sr-only">Decision</legend>
        {CHOICES.map((c) => (
          <label key={c.value} className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${choice === c.value ? "border-brand bg-brand-light" : "border-slate-200"}`}>
            <input type="radio" name="decision" value={c.value} checked={choice === c.value} onChange={() => setChoice(c.value)} className="mt-1" />
            <span><span className="font-medium">{c.label}</span><span className="block text-sm text-ink-soft">{c.hint}</span></span>
          </label>
        ))}
      </fieldset>
      <label className="label mt-3" htmlFor="notes">Notes (kept in the audit trail)</label>
      <textarea id="notes" className="input min-h-24" value={notes} onChange={(e) => setNotes(e.target.value)} />
      {error && <p role="alert" className="mt-2 rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      <button className="btn-primary mt-3" disabled={!choice || busy} onClick={send}>{busy && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />} Submit decision</button>
      <p className="mt-2 text-xs text-ink-faint">A decision cannot be changed once submitted.</p>
    </section>
  );
}
