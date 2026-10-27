"use client";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { blob, type PageSize } from "@/lib/api";

export interface Box { page: number; x: number; y: number; w: number; h: number; id: string; tone?: "finding" | "diff-old" | "diff-new"; active?: boolean }

/**
 * One rendered page of a submitted document with bounding boxes drawn over it. The image is fetched with the bearer token (an <img src>
 * cannot send it) and shown as a blob; boxes are SVG in PDF points (the page size comes from the API), so they stay aligned at any width.
 */
export function PageViewer({ inst, docType, page, size, boxes, onBox }:
  { inst: string; docType: string; page: number; size: PageSize; boxes: Box[]; onBox?: (id: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true, made: string | null = null;
    setUrl(null); setError(null);
    blob(`/review/cases/${encodeURIComponent(inst)}/documents/${encodeURIComponent(docType)}/page/${page}.png?dpi=110`)
      .then((b) => { if (!alive) return; made = URL.createObjectURL(b); setUrl(made); })
      .catch(() => alive && setError("This page could not be shown."));
    return () => { alive = false; if (made) URL.revokeObjectURL(made); };
  }, [inst, docType, page]);

  const fill = { finding: "rgba(220,38,38,0.16)", "diff-old": "rgba(234,88,12,0.18)", "diff-new": "rgba(5,150,105,0.18)" };
  const stroke = { finding: "#b91c1c", "diff-old": "#c2410c", "diff-new": "#047857" };

  if (error) return <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>;
  if (!url) return <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Loading page…</p>;
  const sw = Math.max(size.w, size.h) / 350;
  return (
    <div className="relative mx-auto w-full max-w-3xl">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={`Page ${page} of the document`} className="block w-full rounded border border-slate-300 bg-white" />
      <svg viewBox={`0 0 ${size.w} ${size.h}`} className="absolute inset-0 h-full w-full" aria-label={`Marked areas on page ${page}`}>
        {boxes.filter((b) => b.page === page).map((b) => {
          const t = b.tone ?? "finding";
          return (
            <rect key={b.id} x={b.x} y={b.y} width={b.w} height={b.h} fill={fill[t]} stroke={stroke[t]} strokeWidth={b.active ? sw * 2.5 : sw}
                  className={onBox ? "cursor-pointer" : ""} onClick={() => onBox?.(b.id)} data-box={b.id} />
          );
        })}
      </svg>
    </div>
  );
}
