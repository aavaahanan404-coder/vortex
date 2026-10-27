"use client";
import { useRef, useState, type DragEvent } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, CircleDashed, FileUp, Loader2, ImageIcon } from "lucide-react";
import { api, ApiError, upload, type DocView } from "@/lib/api";
import { checkPhoto } from "@/lib/quality";
import { CropTool } from "./CropTool";

const IMG = /^image\//;

export function UploadCard({ type, label, hint, required, view, uploaded, disabled, onView }:
  { type: string; label: string; hint: string; required: boolean; view?: DocView; uploaded: boolean; disabled: boolean; onView: (v: DocView) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [photo, setPhoto] = useState<File | null>(null);
  const [over, setOver] = useState(false);

  async function send(file: Blob, name: string) {
    setBusy(true); setError(null);
    try { onView(await upload<DocView>(`/apply/documents/${type}`, file, name)); }
    catch (e) { setError(e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : e.message) : "The upload failed. Please try again."); }
    finally { setBusy(false); }
  }

  async function choose(file: File | undefined) {
    if (!file || disabled) return;
    setWarning(null); setError(null);
    if (IMG.test(file.type)) {                       // a photo: instant checks, then the straightening tool
      try { const c = await checkPhoto(file); if (c.warning) setWarning(c.warning); } catch { /* the server will judge */ }
      setPhoto(file);
    } else {
      await send(file, file.name);
    }
  }

  const issues = view?.issues ?? [];
  const status = busy ? "busy" : issues.length ? "attention" : view ? "ok" : uploaded ? "uploaded" : "empty";
  const Icon = status === "ok" ? CheckCircle2 : status === "attention" ? AlertTriangle : status === "empty" ? CircleDashed : status === "busy" ? Loader2 : CheckCircle2;
  const tone = { ok: "text-emerald-700", attention: "text-amber-700", empty: "text-slate-400", busy: "text-brand", uploaded: "text-emerald-700" }[status];
  const prefill = Object.entries(view?.prefill ?? {});

  return (
    <section className="card p-4" aria-labelledby={`h-${type}`}>
      <div className="flex items-start gap-3">
        <Icon aria-hidden className={`mt-0.5 h-5 w-5 shrink-0 ${tone} ${status === "busy" ? "animate-spin" : ""}`} />
        <div className="min-w-0 flex-1">
          <h3 id={`h-${type}`} className="font-medium">{label} {!required && <span className="pill bg-slate-100 text-ink-soft">optional</span>}</h3>
          <p className="text-sm text-ink-soft">{hint}</p>
          {view?.version && <p className="mt-1 text-xs text-ink-faint">Version {view.version} · draft kept for {view.expires_after_hours ?? 48} hours until you submit</p>}
        </div>
        <button className="btn-secondary shrink-0" disabled={disabled || busy} onClick={() => input.current?.click()} aria-label={`${uploaded ? "Replace" : "Upload"} ${label}`}>
          <FileUp aria-hidden className="h-4 w-4" /> {uploaded ? "Replace" : "Upload"}
        </button>
        <input ref={input} type="file" accept="application/pdf,image/*" className="sr-only" tabIndex={-1} aria-hidden
               onChange={(e) => { void choose(e.target.files?.[0]); e.target.value = ""; }} />
      </div>

      {!uploaded && !photo && (
        <div role="group" aria-label={`Drop a file for ${label}`}
             onDragOver={(e: DragEvent) => { e.preventDefault(); if (!disabled) setOver(true); }} onDragLeave={() => setOver(false)}
             onDrop={(e: DragEvent) => { e.preventDefault(); setOver(false); void choose(e.dataTransfer.files?.[0]); }}
             className={`mt-3 rounded-lg border-2 border-dashed p-4 text-center text-sm ${over ? "border-brand bg-brand-light" : "border-slate-300 text-ink-faint"}`}>
          Drop a PDF or a photo here
        </div>
      )}

      {warning && <p role="status" className="mt-3 flex gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900"><ImageIcon aria-hidden className="h-4 w-4 shrink-0" /> {warning}</p>}
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}

      {issues.length > 0 && (
        <ul className="mt-3 space-y-2" aria-label="Things to fix">
          {issues.map((i, k) => (
            <li key={k} className="flex gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900"><AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" /> {i.message}</li>
          ))}
        </ul>
      )}

      {prefill.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="flex cursor-pointer items-center gap-1 text-brand"><ChevronDown aria-hidden className="h-4 w-4" /> Values we read from this document ({prefill.length})</summary>
          <table className="mt-2 w-full text-left">
            <caption className="sr-only">Values read from {label}</caption>
            <tbody>
              {prefill.slice(0, 40).map(([k, v]) => (
                <tr key={k} className={v.confident ? "" : "bg-amber-50"}>
                  <th scope="row" className="w-1/3 py-1 pr-2 align-top font-normal text-ink-faint">{k.replace(/[._]/g, " ")}</th>
                  <td className="py-1">{v.value} {!v.confident && <span className="pill bg-amber-100 text-amber-900">please check</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {prefill.length > 40 && <p className="mt-1 text-xs text-ink-faint">…and {prefill.length - 40} more</p>}
        </details>
      )}

      {photo && (
        <div className="mt-3">
          <CropTool file={photo} onCancel={() => setPhoto(null)}
                    onUse={(flat) => { setPhoto(null); void send(flat, `${type}.png`); }} />
        </div>
      )}
    </section>
  );
}

export { api };
