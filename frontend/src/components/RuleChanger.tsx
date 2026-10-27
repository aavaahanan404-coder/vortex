"use client";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { ArrowDown, ArrowUp, CheckCircle2, Download, FileUp, History, Loader2, Pencil, RotateCcw, ScrollText, TriangleAlert } from "lucide-react";
import { api, blob, jsonBody, upload, type RuleChange, type RulePreview, type RulesState } from "@/lib/api";
import { errorText } from "@/lib/auth";

const show = (v: unknown) => (v === null || v === undefined || v === "TODO" ? "—" : typeof v === "boolean" ? (v ? "on" : "off") : String(v));
const when = (iso: string) => iso.replace("T", " ").replace(/\+00:00$/, " UTC");

function saveBlob(b: Blob, name: string) {
  const u = URL.createObjectURL(b), a = document.createElement("a");
  a.href = u; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(u), 10_000);
}

function Change({ c }: { c: RuleChange }) {
  const Icon = c.direction === "increase" ? ArrowUp : c.direction === "decrease" ? ArrowDown : Pencil;
  const tone = c.direction === "increase" ? "text-emerald-800" : c.direction === "decrease" ? "text-sky-800" : "text-ink-soft";
  const word = c.direction === "increase" ? "up" : c.direction === "decrease" ? "down" : "changed";
  return <span className={`inline-flex items-center gap-1 ${tone}`}><Icon aria-hidden className="h-4 w-4" /> {word}{c.pct !== null && ` ${c.pct > 0 ? "+" : ""}${c.pct}%`}</span>;
}

/** Admin tool: download the current rules as a structured PDF, edit, upload; preview what changes (and what it would do to cases already
 *  in the system); apply with one click; every version is kept and can be restored. Unstructured PDFs are refused with a clear message. */
export function RuleChanger() {
  const [state, setState] = useState<RulesState | null>(null);
  const [preview, setPreview] = useState<RulePreview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [ack, setAck] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => api<RulesState>("/admin/rules").then(setState).catch((e) => setError(errorText(e))), []);
  useEffect(() => { void load(); }, [load]);

  async function run(key: string, f: () => Promise<void>) {
    setBusy(key); setError(null); setNotice(null);
    try { await f(); } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }

  const template = () => run("tpl", async () => saveBlob(await blob("/admin/rules/template.pdf"), "rule_update_template.pdf"));
  const choose = (file?: File) => file && run("up", async () => {
    setPreview(null); setAck(false);
    setPreview(await upload<RulePreview>("/admin/rules/upload", file, file.name, "POST"));
  });
  const apply = () => preview && run("apply", async () => {
    const s = await api<RulesState>("/admin/rules/apply", jsonBody({ id: preview.id, acknowledge: ack }));
    setState(s); setPreview(null); setNotice(`Rules updated. Version ${s.version} is now in force for every new case.`);
  });
  const restore = (v: number) => {
    if (!window.confirm(v === 0 ? "Go back to the rules the system shipped with?" : `Restore the rules as they were in version ${v}?`)) return;
    void run("rev", async () => { const s = await api<RulesState>("/admin/rules/revert", jsonBody({ version: v })); setState(s); setPreview(null); setNotice(`Restored. Version ${s.version} is now in force.`); });
  };

  const impact = preview?.impact;
  const ruleMoves = impact ? Object.entries(impact.rule_changes).flatMap(([rid, m]) => Object.entries(m).map(([k, n]) => `${rid}: ${k} for ${n} case${n > 1 ? "s" : ""}`)) : [];
  const needsAck = (preview?.needs_ack.length ?? 0) > 0;

  return (
    <section className="card space-y-4 p-4" aria-labelledby="rules-h">
      <div>
        <h2 id="rules-h" className="flex items-center gap-2 font-semibold"><ScrollText aria-hidden className="h-5 w-5 text-brand" /> Rules and norms</h2>
        <p className="text-sm text-ink-soft">Change the numbers the checks use (such as students per faculty) and how strongly a rule counts, by uploading a rule PDF. You see exactly what changes before anything is applied.</p>
      </div>

      {state && (
        <p className="rounded-lg bg-slate-50 p-3 text-sm">
          Version <strong>{state.version}</strong> in force{!state.modified && " (the rules as shipped)"} · norms: {state.norms_version ?? "—"}{" "}
          {state.norms_validated ? <span className="pill bg-emerald-100 text-emerald-900">validated</span> : <span className="pill bg-amber-100 text-amber-900">illustrative, not validated</span>}
        </p>
      )}
      {notice && <p role="status" className="flex items-center gap-2 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-900"><CheckCircle2 aria-hidden className="h-4 w-4" /> {notice}</p>}
      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      <ol className="space-y-3 text-sm">
        <li className="flex flex-wrap items-center gap-3">
          <span className="pill bg-brand-light text-brand-dark">1</span><span>Get the current rules as an editable PDF.</span>
          <button className="btn-secondary" onClick={template} disabled={busy !== null}>{busy === "tpl" ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Download aria-hidden className="h-4 w-4" />} Download template</button>
        </li>
        <li>
          <div className="flex flex-wrap items-center gap-3"><span className="pill bg-brand-light text-brand-dark">2</span><span>Upload the rule PDF (the template with your edits, or a table-based update in the same layout).</span></div>
          <div role="group" aria-label="Drop the rule PDF here" onDragOver={(e: DragEvent) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
               onDrop={(e: DragEvent) => { e.preventDefault(); setOver(false); choose(e.dataTransfer.files?.[0]); }}
               className={`mt-2 flex flex-wrap items-center justify-center gap-3 rounded-lg border-2 border-dashed p-5 ${over ? "border-brand bg-brand-light" : "border-slate-300"}`}>
            <button className="btn-secondary" onClick={() => input.current?.click()} disabled={busy !== null}>{busy === "up" ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <FileUp aria-hidden className="h-4 w-4" />} Choose PDF</button>
            <span className="text-ink-faint">or drop it here</span>
            <input ref={input} type="file" accept="application/pdf" className="sr-only" tabIndex={-1} aria-hidden onChange={(e) => { choose(e.target.files?.[0]); e.target.value = ""; }} />
          </div>
          <p className="mt-1 text-xs text-ink-faint">PDFs written as prose or scanned handbooks are not read yet; reading them with AI is planned. Such a file is refused, nothing is changed.</p>
        </li>
      </ol>

      {preview && (
        <div className="space-y-3 rounded-lg border border-slate-200 p-4" role="region" aria-label="Preview of the changes">
          <h3 className="font-semibold">Preview: {preview.source.name}</h3>
          <p className="text-sm">
            {preview.changes.length === 0 ? "Nothing in this file differs from the rules in force." :
              <><strong>{preview.counts.increase}</strong> increased, <strong>{preview.counts.decrease}</strong> decreased, <strong>{preview.counts.changed}</strong> other</>}
            {preview.unchanged > 0 && <> · {preview.unchanged} values already match</>}{preview.rejected.length > 0 && <> · <span className="text-red-800">{preview.rejected.length} row(s) not used</span></>}
          </p>
          {preview.warnings.map((w, i) => <p key={i} className="flex gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900"><TriangleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0" /> {w}</p>)}

          {preview.changes.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Changes this file would make</caption>
                <thead className="border-b border-slate-200 text-ink-faint"><tr><th scope="col" className="py-1 pr-3">What</th><th scope="col" className="pr-3">Was</th><th scope="col" className="pr-3">Becomes</th><th scope="col" className="pr-3">Change</th><th scope="col">Clause</th></tr></thead>
                <tbody>
                  {preview.changes.map((c, i) => (
                    <tr key={i} className="border-b border-slate-100 last:border-0 align-top">
                      <th scope="row" className="py-1 pr-3 font-normal">{c.label}{c.needs_ack.length > 0 && <span className="pill ml-2 bg-amber-100 text-amber-900">confirm</span>}</th>
                      <td className="pr-3 tabular-nums text-ink-soft">{show(c.old)}</td><td className="pr-3 font-medium tabular-nums">{show(c.new)}</td>
                      <td className="pr-3"><Change c={c} /></td><td className="text-ink-soft">{c.ref || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {preview.rejected.length > 0 && (
            <details open className="text-sm">
              <summary className="cursor-pointer font-medium text-red-800">Rows not used ({preview.rejected.length})</summary>
              <ul className="mt-1 space-y-1">{preview.rejected.map((r, i) => <li key={i}><span className="text-ink-faint">page {r.page}:</span> <code className="rounded bg-slate-100 px-1">{r.row}</code> — {r.reason}</li>)}</ul>
            </details>
          )}

          {impact && (
            <div className="rounded-lg bg-slate-50 p-3 text-sm" aria-label="Effect on cases already in the system">
              <p className="font-medium">If applied now, on the {impact.cases_checked} submitted case(s):</p>
              {ruleMoves.length === 0 && impact.category_changes.length === 0 && <p className="text-ink-soft">no rule result and no queue category would change.</p>}
              <ul className="list-disc pl-5">
                {ruleMoves.map((m) => <li key={m}>{m.replace("-> absent", "to switched off").replace("absent ->", "switched back on, now").replace("->", "to")}</li>)}
                {impact.category_changes.map((c) => <li key={c.inst_id}>{c.inst_id} would move from {c.old.replace(/_/g, " ")} to {c.new.replace(/_/g, " ")}</li>)}
              </ul>
              <p className="mt-1 text-xs text-ink-faint">Existing cases are not re-scored automatically; this is only the preview.</p>
            </div>
          )}

          {needsAck && (
            <fieldset className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
              <legend className="px-1 font-medium text-amber-900">These changes can loosen or switch off checks</legend>
              <ul className="list-disc pl-5">{preview.needs_ack.map((n, i) => <li key={i}>{n.label}: {n.why}</li>)}</ul>
              <label className="mt-2 flex items-center gap-2"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have checked these against the handbook and want them applied.</label>
            </fieldset>
          )}

          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={apply} disabled={busy !== null || (preview.changes.length === 0 && Object.keys(preview.meta).length === 0) || (needsAck && !ack)}>
              {busy === "apply" ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <CheckCircle2 aria-hidden className="h-4 w-4" />} Apply to all new cases
            </button>
            <button className="btn-secondary" onClick={() => setPreview(null)} disabled={busy !== null}>Discard</button>
          </div>
        </div>
      )}

      {state && (
        <details className="text-sm">
          <summary className="cursor-pointer font-medium text-brand">Rules and norms now in force</summary>
          <table className="mt-2 w-full text-left">
            <caption className="sr-only">Norms in force</caption>
            <thead className="border-b border-slate-200 text-ink-faint"><tr><th scope="col" className="py-1">Programme</th><th scope="col">Norm</th><th scope="col">Value</th><th scope="col">As shipped</th></tr></thead>
            <tbody>{state.norms.map((n) => <tr key={n.programme_type + n.key} className="border-b border-slate-100"><td className="py-1">{n.programme_type}</td><td>{n.key.replace(/_/g, " ")}</td><td className="font-medium tabular-nums">{n.value}</td><td className="tabular-nums text-ink-soft">{n.shipped}</td></tr>)}</tbody>
          </table>
          <table className="mt-4 w-full text-left">
            <caption className="sr-only">Rule settings in force</caption>
            <thead className="border-b border-slate-200 text-ink-faint"><tr><th scope="col" className="py-1">Rule</th><th scope="col">Counts as</th><th scope="col">Severity</th><th scope="col">p</th><th scope="col">State</th><th scope="col">Clause</th></tr></thead>
            <tbody>{state.rules.map((r) => <tr key={r.id} className="border-b border-slate-100"><td className="py-1"><strong>{r.id}</strong> {r.name.replace(/_/g, " ")}</td><td>{r.kind}</td><td>{r.severity}</td><td className="tabular-nums">{r.p}</td><td>{r.enabled ? "on" : <span className="pill bg-red-100 text-red-800">off</span>}</td><td className="text-ink-soft">{show(r.handbook_ref)}</td></tr>)}</tbody>
          </table>
        </details>
      )}

      {state && (
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold"><History aria-hidden className="h-4 w-4" /> History</h3>
          <ul className="mt-1 space-y-1 text-sm">
            {[...state.history].reverse().map((h) => (
              <li key={h.version} className="flex flex-wrap items-center gap-2">
                <span className="pill bg-slate-100 text-ink-soft">v{h.version}</span>
                <span>{h.kind === "revert" ? `Restored version ${h.restored === 0 ? "0 (as shipped)" : h.restored}` : `${h.n_changes} change(s) from ${h.source_name ?? "a file"}`}</span>
                <span className="text-ink-faint">{when(h.at)} by {h.by}</span>
                {h.version !== state.version && <button className="text-brand underline" onClick={() => restore(h.version)} disabled={busy !== null}>Restore this version</button>}
                {h.version === state.version && <span className="pill bg-emerald-100 text-emerald-900">in force</span>}
              </li>
            ))}
            {state.history.length === 0 && <li className="text-ink-soft">No changes yet: the rules are as shipped.</li>}
          </ul>
          {state.modified && <button className="btn-secondary mt-2" onClick={() => restore(0)} disabled={busy !== null}><RotateCcw aria-hidden className="h-4 w-4" /> Restore shipped rules</button>}
        </div>
      )}
    </section>
  );
}
