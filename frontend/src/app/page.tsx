"use client";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, Loader2 } from "lucide-react";
import { homeFor, useAuth, errorText } from "@/lib/auth";

export default function LoginPage() {
  const { me, loading, login } = useAuth();
  const router = useRouter();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (!loading && me) router.replace(homeFor(me.role)); }, [me, loading, router]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { const m = await login(token); router.replace(homeFor(m.role)); }
    catch (err) { setError(errorText(err) === "unknown token" ? "That access token was not recognised." : errorText(err)); }
    finally { setBusy(false); }
  }

  return (
    <div className="mx-auto mt-10 max-w-md">
      <form onSubmit={submit} className="card space-y-4 p-6" aria-labelledby="login-title">
        <h1 id="login-title" className="text-xl font-semibold">Sign in</h1>
        <p className="text-sm text-ink-soft">
          Applicants upload and check their documents here. Reviewers and supervisors open the review dashboard.
        </p>
        <div>
          <label htmlFor="token" className="label">Access token</label>
          <input id="token" className="input font-mono" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off"
                 spellCheck={false} placeholder="Paste your access token" required />
        </div>
        {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
        <button className="btn-primary w-full" disabled={busy || !token.trim()}>
          {busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <KeyRound aria-hidden className="h-4 w-4" />} Continue
        </button>
        <p className="rounded-lg bg-amber-50 p-3 text-xs text-amber-900">
          Development build: access tokens are static. A deployment signs in through the organisation&apos;s single sign-on instead.
        </p>
      </form>
    </div>
  );
}
