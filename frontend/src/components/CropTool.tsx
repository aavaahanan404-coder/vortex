"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Crop, Loader2, RotateCcw, Check, X } from "lucide-react";
import { ApiError, upload, uploadForBlob, type PhotoDetect } from "@/lib/api";

type Pt = [number, number];
const NAMES = ["Top-left corner", "Top-right corner", "Bottom-right corner", "Bottom-left corner"];

/**
 * Photo straightening with draggable corner handles. The server finds the page corners (modules/perspective); the person can move them
 * (mouse, touch or arrow keys on a focused handle: 2 px, Shift = 10 px) and the server warps the photo flat with the final corners.
 * The overlay is SVG in image-pixel coordinates over the photo, so it scales with the picture.
 */
export function CropTool({ file, onUse, onCancel }: { file: File; onUse: (flat: Blob) => void; onCancel: () => void }) {
  const [url, setUrl] = useState<string>("");
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [corners, setCorners] = useState<Pt[]>([]);
  const [detect, setDetect] = useState<PhotoDetect | null>(null);
  const [busy, setBusy] = useState<"detect" | "warp" | null>("detect");
  const [error, setError] = useState<string | null>(null);
  const [flat, setFlat] = useState<{ blob: Blob; url: string } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<number | null>(null);

  useEffect(() => {
    const u = URL.createObjectURL(file);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);

  useEffect(() => {
    let alive = true;
    setBusy("detect"); setError(null);
    upload<PhotoDetect>("/apply/photo/detect", file, file.name, "POST")
      .then((d) => { if (!alive) return; setDetect(d); setSize({ w: d.width, h: d.height }); setCorners(d.corners.map((c) => [c[0], c[1]] as Pt)); })
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "The photo could not be analysed."))
      .finally(() => alive && setBusy(null));
    return () => { alive = false; };
  }, [file]);

  const toImage = useCallback((ev: { clientX: number; clientY: number }): Pt => {
    const r = svgRef.current!.getBoundingClientRect();
    return [((ev.clientX - r.left) / r.width) * size!.w, ((ev.clientY - r.top) / r.height) * size!.h];
  }, [size]);

  const move = (i: number, p: Pt) => setCorners((cs) => cs.map((c, k) => (k === i ? [Math.max(0, Math.min(size!.w, p[0])), Math.max(0, Math.min(size!.h, p[1]))] : c)));

  async function straighten() {
    setBusy("warp"); setError(null);
    try {
      const b = await uploadForBlob("/apply/photo/warp", file, file.name, `?corners=${encodeURIComponent(JSON.stringify(corners))}`);
      setFlat({ blob: b, url: URL.createObjectURL(b) });
    } catch (e) { setError(e instanceof ApiError ? e.message : "The page could not be straightened."); }
    finally { setBusy(null); }
  }

  const poly = corners.map((c) => c.join(",")).join(" ");
  const handle = size ? Math.max(size.w, size.h) / 55 : 10;

  if (flat) {
    return (
      <div className="card space-y-3 p-4" role="dialog" aria-label="Straightened page preview">
        <h3 className="flex items-center gap-2 font-semibold"><Check aria-hidden className="h-5 w-5 text-emerald-700" /> Straightened page</h3>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={flat.url} alt="The straightened page" className="mx-auto max-h-[60vh] rounded border border-slate-200" />
        <p className="text-sm text-ink-soft">Check that the page is upright and the text is readable. If a corner is cut off, adjust and straighten again.</p>
        <div className="flex flex-wrap gap-2">
          <button className="btn-primary" onClick={() => onUse(flat.blob)}><Check aria-hidden className="h-4 w-4" /> Use this page</button>
          <button className="btn-secondary" onClick={() => { URL.revokeObjectURL(flat.url); setFlat(null); }}><RotateCcw aria-hidden className="h-4 w-4" /> Adjust again</button>
          <button className="btn-secondary" onClick={onCancel}><X aria-hidden className="h-4 w-4" /> Cancel</button>
        </div>
      </div>
    );
  }

  return (
    <div className="card space-y-3 p-4" role="dialog" aria-label="Straighten the photo">
      <h3 className="flex items-center gap-2 font-semibold"><Crop aria-hidden className="h-5 w-5 text-brand" /> Straighten the photo</h3>
      {busy === "detect" && <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Looking for the edges of the page…</p>}
      {detect && <p className={`rounded-lg p-3 text-sm ${detect.found ? "bg-brand-light text-brand-dark" : "bg-amber-50 text-amber-900"}`} role="status">{detect.message}</p>}
      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {size && url && (
        <div className="relative mx-auto w-full max-w-3xl select-none touch-none">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt="Your photo" className="block w-full rounded border border-slate-200" draggable={false} />
          <svg ref={svgRef} viewBox={`0 0 ${size.w} ${size.h}`} className="absolute inset-0 h-full w-full"
               onPointerMove={(ev) => { if (drag.current !== null) move(drag.current, toImage(ev)); }}
               onPointerUp={() => { drag.current = null; }} onPointerLeave={() => { drag.current = null; }}>
            <polygon points={poly} fill="rgba(31,78,121,0.12)" stroke="#1f4e79" strokeWidth={handle / 6} />
            {corners.map((c, i) => (
              <circle key={i} cx={c[0]} cy={c[1]} r={handle} tabIndex={0} role="slider" aria-label={`${NAMES[i]}, drag or use the arrow keys`}
                      aria-valuetext={`x ${Math.round(c[0])}, y ${Math.round(c[1])}`} aria-valuemin={0} aria-valuenow={Math.round(c[0])}
                      fill="#fff" stroke="#c2410c" strokeWidth={handle / 4} className="cursor-grab focus:outline-none focus-visible:stroke-[#1f4e79]"
                      onPointerDown={(ev) => { (ev.target as Element).setPointerCapture(ev.pointerId); drag.current = i; }}
                      onKeyDown={(ev) => {
                        const step = ev.shiftKey ? 10 : 2;
                        const d: Record<string, Pt> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
                        if (d[ev.key]) { ev.preventDefault(); move(i, [c[0] + d[ev.key][0] * (size.w / 800), c[1] + d[ev.key][1] * (size.w / 800)]); }
                      }} />
            ))}
          </svg>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" disabled={!size || busy !== null} onClick={straighten}>
          {busy === "warp" ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Crop aria-hidden className="h-4 w-4" />} Straighten
        </button>
        <button className="btn-secondary" disabled={!detect} onClick={() => detect && setCorners(detect.corners.map((c) => [c[0], c[1]] as Pt))}><RotateCcw aria-hidden className="h-4 w-4" /> Reset corners</button>
        <button className="btn-secondary" onClick={onCancel}><X aria-hidden className="h-4 w-4" /> Cancel</button>
      </div>
      <p className="text-xs text-ink-faint">Tip: photograph the whole page flat, on a plain background, with even light. Drag the orange circles onto the four corners of the page.</p>
    </div>
  );
}
