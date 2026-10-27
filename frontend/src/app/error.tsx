"use client";
import { useEffect } from "react";
import { TriangleAlert } from "lucide-react";

/** A rendering fault in one page must not blank the whole app with the browser's generic message: show a plain notice and a way back. */
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  return (
    <div role="alert" className="card mx-auto mt-10 max-w-lg space-y-3 p-6">
      <h1 className="flex items-center gap-2 text-lg font-semibold"><TriangleAlert aria-hidden className="h-5 w-5 text-amber-700" /> This page could not be shown</h1>
      <p className="text-sm text-ink-soft">Something unexpected went wrong while drawing this page. Nothing was changed. Try again, or go back to your list.</p>
      <div className="flex gap-2">
        <button className="btn-primary" onClick={reset}>Try again</button>
        <a className="btn-secondary" href="/">Back to start</a>
      </div>
    </div>
  );
}
