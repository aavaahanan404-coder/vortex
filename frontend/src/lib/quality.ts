// Instant, in-browser checks on a photo before it is uploaded. They are advisory (the server decides); they only save a round trip.

export interface PhotoCheck { ok: boolean; blur: number; width: number; height: number; warning?: string }

/** Variance of the Laplacian of the grey image scaled to 800 px wide: the same blur measure the server uses (different scale). */
export async function checkPhoto(file: File): Promise<PhotoCheck> {
  const bmp = await createImageBitmap(file);
  const bw = bmp.width, bh = bmp.height;
  const w = 800, h = Math.max(1, Math.round((bh * w) / bw));
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  const ctx = cv.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(bmp, 0, 0, w, h);
  const { data } = ctx.getImageData(0, 0, w, h);
  const g = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) g[i] = 0.299 * data[4 * i] + 0.587 * data[4 * i + 1] + 0.114 * data[4 * i + 2];
  let sum = 0, sum2 = 0, n = 0;
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x;
    const l = g[i - 1] + g[i + 1] + g[i - w] + g[i + w] - 4 * g[i];
    sum += l; sum2 += l * l; n++;
  }
  const blur = sum2 / n - (sum / n) ** 2;
  const small = Math.min(bw, bh) < 700;
  const warning = blur < BLUR_MIN ? "This photo looks blurry. Hold the phone steady, tap to focus and retake it if you can."
    : small ? "This photo has a low resolution. A larger photo reads more reliably." : undefined;
  bmp.close();
  return { ok: !warning, blur, width: bw, height: bh, warning };
}

// Indicative threshold for an 800 px grey copy of a typical phone photo of a page; the server's gate (per document type) is authoritative.
export const BLUR_MIN = 25;
