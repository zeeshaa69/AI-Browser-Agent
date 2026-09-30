export interface LogEntry {
  timestamp: string;
  step: number | null;
  action: string;
  target: string;
  result: "ok" | "failed" | "blocked" | "retry" | "skipped" | "info";
  error: string | null;
  retry_count: number;
  duration_ms: number;
  verified: boolean | null;
  detail: string;
  screenshot: string | null;
}

export interface StepState {
  id: number;
  description: string;
  action: string;
  state: "pending" | "running" | "done" | "failed" | "blocked";
  error: string | null;
}

export interface PendingConfirmation {
  step: number;
  description: string;
}

export interface TaskState {
  id: string;
  goal: string;
  status: string;
  phase: string;
  current_step: number | null;
  finished: boolean;
  plan_notes: string[];
  planner: string | null;
  steps: StepState[];
  browser: { state: string; url: string; title: string };
  log: LogEntry[];
  log_total: number;
  screenshots: string[];
  extracted: Record<string, unknown>;
  error: string | null;
  pending_confirmation: PendingConfirmation | null;
  result: { steps_completed: number; steps_total: number; actions_used: number; duration_s: number; final_url: string } | null;
  format: string;
}

export interface TaskRequest {
  goal: string;
  start_url?: string;
  allowed_domains: string[];
  max_actions: number;
  format: string;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  config: () => fetch("/api/config").then((r) => json<{ demo_url: string | null; default_goal: string }>(r)),
  start: (req: TaskRequest) => post("/api/tasks", req).then((r) => json<{ id: string }>(r)),
  state: (id: string, since: number) => fetch(`/api/tasks/${id}?since=${since}`).then((r) => json<TaskState>(r)),
  confirm: (id: string, approve: boolean) => post(`/api/tasks/${id}/confirm`, { approve }).then((r) => json<{ ok: boolean }>(r)),
  cancel: (id: string) => post(`/api/tasks/${id}/cancel`, {}).then((r) => json<{ ok: boolean }>(r)),
  exportUrl: (id: string, format: string) => `/api/tasks/${id}/export?format=${format}`,
  exportText: (id: string, format: string) => fetch(`/api/tasks/${id}/export?format=${format}`).then((r) => r.text()),
  screenshotUrl: (id: string, name: string) => `/api/tasks/${id}/screenshots/${encodeURIComponent(name)}`,
};
