"use client";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { AlertTriangle, CheckCircle2, ClipboardCheck, Clock, Loader2, Send } from "lucide-react";
import { api, ApiError, DOC_TYPES, jsonBody, type ClientView, type DocView, type MyApplication } from "@/lib/api";
import { errorText, useRequireRole } from "@/lib/auth";
import { UploadCard } from "@/components/UploadCard";

type Phase = "loading" | "none" | "ready";

export default function ApplyPage() {
  const { me, ready } = useRequireRole("applicant");
  const [phase, setPhase] = useState<Phase>("loading");
  const [mine, setMine] = useState<MyApplication | null>(null);
  const [views, setViews] = useState<Record<string, DocView>>({});
  const [check, setCheck] = useState<ClientView | null>(null);
  const [busy, setBusy] = useState<"check" | "submit" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const m = await api<MyApplication>("/apply/application");
      setMine(m); setPhase("ready");
      if (m.documents.length && (m.status === "draft" || m.status === "info_requested")) {          // restore what each upload looks like
        const v = await api<ClientView>("/apply/dry-run", { method: "POST" });
        setViews((cur) => ({ ...Object.fromEntries(v.documents.map((d) => [d.doc_type, d])), ...cur }));
      }
    } catch (e) { if (e instanceof ApiError && e.status === 404) setPhase("none"); else setError(errorText(e)); }
  }, []);

  useEffect(() => { if (ready) void refresh(); }, [ready, refresh]);

  const editable = mine?.status === "draft" || mine?.status === "info_requested";
  const uploaded = useMemo(() => new Set(mine?.documents ?? []), [mine]);
  const required = DOC_TYPES.filter((d) => d.required);
  const done = required.filter((d) => uploaded.has(d.type)).length;

  async function runCheck() {
    setBusy("check"); setError(null);
    try {
      const v = await api<ClientView>("/apply/dry-run", { method: "POST" });
      setCheck(v); setViews((cur) => ({ ...cur, ...Object.fromEntries(v.documents.map((d) => [d.doc_type, d])) }));
    } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }

  async function submit() {
    setBusy("submit"); setError(null);
    try {
      await api<ClientView>("/apply/submit", { method: "POST" });
      setCheck(null); await refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.detail && typeof e.detail === "object" && "view" in (e.detail as object)) {
        const v = (e.detail as { view: ClientView }).view;
        setCheck(v); setViews((cur) => ({ ...cur, ...Object.fromEntries(v.documents.map((d) => [d.doc_type, d])) }));
        setError("Your application is not ready to submit yet. Please fix the points below.");
      } else setError(errorText(e));
    } finally { setBusy(null); }
  }

  if (!ready || phase === "loading") return <p className="flex items-center gap-2 text-ink-soft"><Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading…</p>;
  if (phase === "none") return <CreateApplication onCreated={() => { setPhase("loading"); void refresh(); }} instId={me?.inst_id ?? ""} />;

  const banners: Record<string, { tone: string; icon: typeof AlertTriangle; text: string } | null> = {
    draft: null,
    info_requested: { tone: "bg-amber-50 text-amber-900", icon: AlertTriangle, text: "A reviewer asked for more information. Replace the documents they need and submit again." },
    received: { tone: "bg-emerald-50 text-emerald-900", icon: CheckCircle2, text: "Your application was received and is being reviewed. You will be told if anything else is needed." },
    decision_made: { tone: "bg-brand-light text-brand-dark", icon: ClipboardCheck, text: `A decision has been recorded: ${mine?.decision ?? "see your approval authority"}.` },
  };
  const banner = banners[mine!.status] ?? null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Your application</h1>
        <p className="text-sm text-ink-soft">Upload each document. We check it straight away and tell you what to fix; nothing is final until you submit.</p>
      </div>

      {banner && <p role="status" className={`flex gap-2 rounded-lg p-3 text-sm ${banner.tone}`}><banner.icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0" /> {banner.text}</p>}

      <div className="card flex flex-wrap items-center gap-4 p-4">
        <div className="min-w-[12rem] flex-1">
          <p className="text-sm font-medium">{done} of {required.length} required documents uploaded</p>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuemin={0} aria-valuemax={required.length} aria-valuenow={done} aria-label="Documents uploaded">
            <div className="h-full bg-brand transition-all" style={{ width: `${(done / required.length) * 100}%` }} />
          </div>
          {editable && <p className="mt-2 flex items-center gap-1 text-xs text-ink-faint"><Clock aria-hidden className="h-3 w-3" /> Draft uploads are deleted automatically 48 hours after upload unless you submit.</p>}
        </div>
        {editable && (
          <div className="flex gap-2">
            <button className="btn-secondary" disabled={busy !== null || uploaded.size === 0} onClick={runCheck}>
              {busy === "check" ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <ClipboardCheck aria-hidden className="h-4 w-4" />} Check my application
            </button>
            <button className="btn-primary" disabled={busy !== null || done < required.length} onClick={submit}>
              {busy === "submit" ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Send aria-hidden className="h-4 w-4" />} Submit
            </button>
          </div>
        )}
      </div>

      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {check && (
        <div className="card p-4" role="region" aria-label="Result of the check">
          {check.status === "action_needed" ? (
            <>
              <h2 className="flex items-center gap-2 font-semibold text-amber-800"><AlertTriangle aria-hidden className="h-5 w-5" /> A few things to fix</h2>
              <ul className="mt-2 list-disc space-y-1 pl-6 text-sm">
                {check.application_issues.map((i, k) => <li key={`a${k}`}>{i.message}</li>)}
                {check.documents.flatMap((d) => d.issues.map((i, k) => <li key={`${d.doc_type}${k}`}><strong>{DOC_TYPES.find((t) => t.type === d.doc_type)?.label}:</strong> {i.message}</li>))}
              </ul>
            </>
          ) : (
            <h2 className="flex items-center gap-2 font-semibold text-emerald-800"><CheckCircle2 aria-hidden className="h-5 w-5" /> Everything looks fine. You can submit.</h2>
          )}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {DOC_TYPES.map((d) => (
          <UploadCard key={d.type} type={d.type} label={d.label} hint={d.hint} required={d.required} view={views[d.type]}
                      uploaded={uploaded.has(d.type)} disabled={!editable}
                      onView={(v) => { setViews((cur) => ({ ...cur, [d.type]: v })); setCheck(null); void api<MyApplication>("/apply/application").then(setMine); }} />
        ))}
      </div>
    </div>
  );
}

function CreateApplication({ onCreated, instId }: { onCreated: () => void; instId: string }) {
  const [f, setF] = useState({ name: "", application_date: new Date().toISOString().slice(0, 10), application_type: "existing", programme_type: "engineering" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try { await api("/apply/application", jsonBody(f)); onCreated(); } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="card mx-auto max-w-lg space-y-4 p-6">
      <h1 className="text-xl font-semibold">Start your application</h1>
      <p className="text-sm text-ink-soft">Institution {instId}. Tell us a little about the application; you can then upload your documents.</p>
      <div><label className="label" htmlFor="name">Institution name</label><input id="name" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></div>
      <div className="grid grid-cols-2 gap-3">
        <div><label className="label" htmlFor="date">Application date</label><input id="date" type="date" className="input" value={f.application_date} onChange={(e) => setF({ ...f, application_date: e.target.value })} required /></div>
        <div><label className="label" htmlFor="atype">Application type</label>
          <select id="atype" className="input" value={f.application_type} onChange={(e) => setF({ ...f, application_type: e.target.value })}><option value="existing">Existing institution</option><option value="new">New institution</option></select></div>
      </div>
      <div><label className="label" htmlFor="ptype">Type of institution</label>
        <select id="ptype" className="input" value={f.programme_type} onChange={(e) => setF({ ...f, programme_type: e.target.value })}>
          <option value="engineering">Engineering</option><option value="mba">Management (MBA)</option><option value="polytechnic">Polytechnic</option></select></div>
      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      <button className="btn-primary w-full" disabled={busy}>{busy && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />} Create application</button>
    </form>
  );
}
