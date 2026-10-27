// Typed client for the FastAPI gateway. The browser calls /backend/* (proxied by next.config.mjs); the token is kept in sessionStorage.

export type Role = "applicant" | "reviewer" | "supervisor" | "admin";
export interface Me { role: Role; user?: string; inst_id?: string }

export interface Issue { code: string; message: string; where?: { page: number; box: number[] } | null }
export interface Prefill { [key: string]: { value: string; confident: boolean } }
export interface DocView { doc_id?: string; doc_type: string; status: "ok" | "needs_attention"; issues: Issue[]; prefill: Prefill; version?: number; expires_after_hours?: number }
export interface ClientView {
  mode: string; application: string; status: "action_needed" | "ready_to_submit" | "received";
  documents: DocView[]; application_issues: { code: string; message: string; detail?: string }[];
}
export interface MyApplication { application: string; status: string; documents: string[]; decision?: string }
export interface PhotoDetect { found: boolean; width: number; height: number; corners: number[][]; status?: string; message: string }

export interface Finding { id: string; doc_id: string; tier: number; message: string; bbox: number[] | null; evidence: Record<string, unknown>; kind?: string; p?: number }
export interface QueueRow { inst_id: string; name?: string; status: string; score: number; category: string; double_review: boolean; phase: string; assigned: string[] }
export interface MyCase { inst_id: string; phase: string; you_submitted: boolean }
export interface PageSize { w: number; h: number }
export interface CaseDoc { doc_id: string; doc_type: string; fields: Record<string, { value: string; conf?: number; flags?: string[]; alt_values?: string[] }>; pages: PageSize[] }
export interface ReviewerCase {
  case_id: string; phase: string; your_decision: string | null; your_notes: string | null; n_reviewers: number; submitted: number | string[];
  score: number | null; category: string | null; reasons: { finding_id: string; message: string; contribution: number }[] | null;
  outcome?: string | null; decisions?: Record<string, string>; notes?: Record<string, string>;
  application: { inst_id: string; name?: string; application_date: string; application_type?: string };
  documents: CaseDoc[]; findings: Finding[]; findings_with_scores?: Finding[];
}
export interface SupervisorCase {
  result: { app: Record<string, string>; documents: (CaseDoc & { path: string })[]; findings: Finding[]; rules: { id: string; name: string; result: string; detail: string }[];
            score: { score: number; category: string; reasons: { finding_id: string; message: string; contribution: number }[] }; review: { double_review: boolean } };
  review: { phase: string; assigned: string[]; decisions: Record<string, string>; notes: Record<string, string>; outcome: string | null; double: boolean };
  pages: Record<string, PageSize[]>;
}

const KEY = "sih26261.token";
export const tokenStore = {
  get: () => (typeof window === "undefined" ? null : window.sessionStorage.getItem(KEY)),
  set: (t: string) => window.sessionStorage.setItem(KEY, t),
  clear: () => window.sessionStorage.removeItem(KEY),
};

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown, message: string) { super(message); }
}

function messageOf(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in (detail as Record<string, unknown>)) return String((detail as Record<string, unknown>).message);
  return `Request failed (${status})`;
}

async function request(path: string, init: RequestInit = {}, token?: string | null): Promise<Response> {
  const t = token ?? tokenStore.get();
  const headers = new Headers(init.headers);
  if (t) headers.set("Authorization", `Bearer ${t}`);
  return fetch(`/backend${path}`, { ...init, headers });
}

export async function api<T>(path: string, init: RequestInit = {}, token?: string | null): Promise<T> {
  const r = await request(path, init, token);
  if (!r.ok) {
    let detail: unknown = null;
    try { detail = (await r.json()).detail; } catch { /* not JSON */ }
    throw new ApiError(r.status, detail, messageOf(detail, r.status));
  }
  return (await r.json()) as T;
}

export const jsonBody = (v: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) });

export async function blob(path: string): Promise<Blob> {
  const r = await request(path);
  if (!r.ok) throw new ApiError(r.status, null, `Request failed (${r.status})`);
  return r.blob();
}

export async function upload<T>(path: string, file: Blob, filename: string, method = "PUT", query = ""): Promise<T> {
  const fd = new FormData();
  fd.append("file", file, filename);
  return api<T>(`${path}${query}`, { method, body: fd });
}

/** POST a file and get an image back (the straightened page). */
export async function uploadForBlob(path: string, file: Blob, filename: string, query = ""): Promise<Blob> {
  const fd = new FormData();
  fd.append("file", file, filename);
  const r = await request(`${path}${query}`, { method: "POST", body: fd });
  if (!r.ok) {
    let detail: unknown = null;
    try { detail = (await r.json()).detail; } catch { /* not JSON */ }
    throw new ApiError(r.status, detail, messageOf(detail, r.status));
  }
  return r.blob();
}

export const DOC_TYPES: { type: string; label: string; required: boolean; hint: string }[] = [
  { type: "society_reg", label: "Society / trust registration certificate", required: true, hint: "Registration of the society or trust that runs the institution" },
  { type: "land_doc", label: "Land document (sale deed / lease deed)", required: true, hint: "Title or lease deed of the campus land" },
  { type: "building_plan", label: "Building plan approval", required: true, hint: "Municipal approval with its QR code" },
  { type: "fire_cert", label: "Fire safety certificate", required: true, hint: "Current certificate with its QR code" },
  { type: "aff_letter", label: "University affiliation letter", required: true, hint: "Affiliation with approved intake per course" },
  { type: "faculty_list", label: "Faculty list", required: true, hint: "Name, designation, qualification, PAN, date of joining" },
  { type: "infra_statement", label: "Infrastructure statement", required: true, hint: "Built-up area and computers available" },
  { type: "financial", label: "Audited financial statement", required: true, hint: "With the CA firm number and UDIN" },
  { type: "affidavit", label: "Application affidavit", required: true, hint: "Courses and intake applied for" },
  { type: "eoa", label: "Previous approval (EoA)", required: false, hint: "Only for existing institutions" },
];

export const DOC_LABEL: Record<string, string> = Object.fromEntries(DOC_TYPES.map((d) => [d.type, d.label]));

// ---- rule changer (/admin/rules)
export interface RuleChange { target: "norm" | "rule"; label: string; key: string; old: unknown; new: unknown; direction: "increase" | "decrease" | "changed"; pct: number | null; ref: string; page: number; needs_ack: string[]; quote?: string }
export interface RuleHistory { version: number; kind: "apply" | "revert"; at: string; by: string; source_name?: string | null; n_changes?: number; restored?: number }
export interface RulesState {
  version: number; modified: boolean; norms_version: string | null; norms_validated: boolean;
  norms: { programme_type: string; key: string; value: number; shipped: number | null; ref: string | null }[];
  rules: { id: string; name: string; tier: number; enabled: boolean; kind: string; severity: string; p: number; handbook_ref: string | null }[];
  history: RuleHistory[];
}
export interface RulePreview {
  id: string; based_on_version: number; meta: Record<string, unknown>; changes: RuleChange[]; unchanged: number; warnings: string[];
  rejected: { page: number; row: string; reason: string }[]; counts: { increase: number; decrease: number; changed: number };
  needs_ack: { label: string; why: string }[]; source: { name?: string }; ai?: boolean; ai_notes?: string; yaml?: string;
  impact: { cases_checked: number; rule_changes: Record<string, Record<string, number>>; category_changes: { inst_id: string; old: string; new: string }[]; score_changes: number } | null;
}

// ---- AI assistant (Groq, OpenRouter or Gemini; off unless the server has a key)
export interface AiStatus { enabled: boolean; model: string | null; service: string }
export interface AiCaseExplanation { summary: string; most_important: string; findings: { id: string; meaning: string; what_to_check: string }[]; caveat: string; model: string; blind: boolean; cached?: boolean; service: string }
export interface AiPlanExplanation { summary: string; watch_out: string[]; caveat: string; model: string; service: string }
