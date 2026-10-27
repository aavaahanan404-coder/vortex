"use client";
import { useEffect, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { api, type AiCaseExplanation, type AiStatus } from "@/lib/api";
import { errorText } from "@/lib/auth";

/** Optional AI help: a plain-words explanation of the anomalies of one case. Hidden unless the server has a Gemini key. The answer is
 *  evidence-based text only; it never decides, and for a blind reviewer it is built without the machine's score. */
export function AiExplain({ inst, blind, onResult }: { inst: string; blind: boolean; onResult: (r: AiCaseExplanation | null) => void }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [out, setOut] = useState<AiCaseExplanation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api<AiStatus>("/ai/status").then(setStatus).catch(() => setStatus(null)); }, []);
  if (!status) return null;

  async function run() {
    setBusy(true); setError(null);
    try { const r = await api<AiCaseExplanation>(`/review/cases/${encodeURIComponent(inst)}/ai-explain`, { method: "POST" }); setOut(r); onResult(r); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }

  return (
    <section className="card space-y-3 p-4" aria-labelledby="ai-h">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="ai-h" className="flex items-center gap-2 font-semibold"><Sparkles aria-hidden className="h-5 w-5 text-brand" /> AI explanation</h2>
        {status.enabled ? (
          <button className="btn-secondary" onClick={run} disabled={busy}>{busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Sparkles aria-hidden className="h-4 w-4" />} {out ? "Explain again" : "Explain these anomalies"}</button>
        ) : <span className="pill bg-slate-100 text-ink-soft">not set up on this server</span>}
      </div>
      {!status.enabled && <p className="text-sm text-ink-soft">The administrator can switch this on by giving the server a Gemini API key.</p>}
      {status.enabled && !out && !error && (
        <p className="text-sm text-ink-soft">Asks {status.service} to explain the findings of this case in plain words. Only the finding messages and their evidence are sent, not the documents.{blind && " You are reviewing blind, so the explanation is written without the machine's score."}</p>
      )}
      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {out && (
        <div className="space-y-2 text-sm" aria-live="polite">
          <p>{out.summary}</p>
          {out.most_important && <p className="rounded-lg bg-brand-light p-3 text-brand-dark"><strong>Look first at:</strong> {out.most_important}</p>}
          {out.findings.length > 0 && <p className="text-ink-soft">Each finding below now has a short note from the AI.</p>}
          <p className="text-xs text-ink-faint">{out.caveat} Model: {out.model}. {out.cached ? "Shown from an earlier request. " : ""}Sent to {out.service}.</p>
        </div>
      )}
    </section>
  );
}
