export type Dataset = { id: string; name: string; filename: string; format: string; sha256: string; nrow: number; ncol: number; source: string; created_at: number }
export type Column = { name: string; label: string; type: string; n_missing: number; n_distinct: number; min: number | null; max: number | null; top: { value: string; n: number }[] }
export type Profile = { ok: boolean; nrow: number; ncol: number; label: string; columns: Column[] }
export type Program = { version: number; author: 'agent' | 'user'; code: string; sha256: string; summary: string; created_at: number }
export type Cond = { var: string; op: string; value?: unknown }
export type Spec = {
  type: string; title?: string; datasets: { subject: string; events: string }; subject_id?: string
  population?: Cond[]; treatment_var: string; event_filter?: Cond[]; soc_var: string; pt_var: string; include_total?: boolean
}
export type SpecRow = { version: number; spec: Spec; status: 'proposed' | 'approved' | 'rejected' | 'superseded'; rationale: string; proposed_by: string; decided_by: string | null; created_at: number; decided_at: number | null }
export type Table = { id: string; title: string; type: string; columns: string[]; rows: Record<string, unknown>[] }
export type RunError = { ok: false; kind: string; message: string; line?: number | null; end_line?: number | null }
export type Run = { id: string; program_version: number; status: string; started: number; ended: number; console: string; result: Record<string, unknown> & { warnings?: { line: number; end_line: number; message: string }[] }; tables: Table[]; provenance: { program_sha256: string; datasets: Record<string, string>; backend: string; r_version?: string; packages?: Record<string, string> } }
export type Cell = { level: string; soc: string | null; pt: string | null; group: string; n: number | null; N: number | null; ref_n?: number | null; ref_N?: number | null; status: 'passed' | 'mismatch' | 'not_verified'; reason?: string | null }
export type TableValidation = { table_id: string; status: 'passed' | 'mismatch' | 'not_verified'; passed: number; mismatch: number; not_verified: number; cells: Cell[]; reason?: string; groups?: string[]; population_n?: number; pattern?: string[] }
export type Validation = { validation_id: string; run_id: string; spec_version: number | null; program_version: number; tables: TableValidation[] }
export type Approval = { id: string; run_id: string; validation_id: string | null; reviewer: string; decision: string; note: string; created_at: number }
export type Snapshot = {
  workspace: { id: string; name: string; created_at: number }
  datasets: Dataset[]; program: Program | null; programs: Omit<Program, 'code'>[]; specs: SpecRow[]
  run: Run | null; validation: Validation | null; approvals: Approval[]; agent_busy: boolean; last_event: number
}
export type Trace = {
  run_id: string; table_id: string; program_version: number; cell: { level: string; soc: string | null; pt: string | null; group: string }
  program_value: { n: unknown; N: unknown } | null; note?: string; spec_version?: number
  source?: { subject: string; events: string }; population?: string[]; group?: string; event_filter?: string[]
  numerator?: string; denominator?: string
  reference: { n: number; N: number; subjects: string[]; record_count: number } | null
  records_sample?: Record<string, string>[]; code_lines?: { line: number; text: string; terms: string[] }[]
}
export type Relationship = { a: string; b: string; shared_columns: string[]; keys: { column: string; parent: string; child: string; child_distinct: number; matched: number; parent_without_child: number }[] }
export type Health = { r: string; executor: string; llm: { base_url: string; model: string; has_key: boolean; configured: boolean; host: string }; acked_hosts: string[] }
export type Ev = { id: number | null; ts?: number; kind: string; payload: Record<string, any> }

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) { init.body = JSON.stringify(body); init.headers = { 'Content-Type': 'application/json' } }
  const r = await fetch(url, init)
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* not JSON */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

const w = (id: string) => `/api/workspaces/${id}`
export const api = {
  health: () => req<Health>('GET', '/api/health'),
  setLLM: (b: { base_url: string; model: string; api_key?: string | null }) => req<Health['llm']>('PUT', '/api/settings/llm', b),
  testLLM: () => req<{ ok: boolean; models?: string[]; model_available?: boolean; error?: string }>('POST', '/api/settings/llm/test'),
  ack: (host: string) => req('POST', '/api/settings/ack', { host }),
  workspaces: () => req<{ id: string; name: string; created_at: number; datasets: number; last_active: number | null }[]>('GET', '/api/workspaces'),
  createWorkspace: (name: string) => req<{ id: string; name: string }>('POST', '/api/workspaces', { name }),
  snapshot: (id: string) => req<Snapshot>('GET', w(id)),
  upload: (id: string, files: File[]) => { const f = new FormData(); files.forEach(x => f.append('files', x)); return req<{ datasets: Dataset[]; errors: { file: string; error: string }[] }>('POST', `${w(id)}/datasets`, f) },
  pilot: (id: string) => req<{ datasets: Dataset[] }>('POST', `${w(id)}/datasets/pilot`),
  profile: (id: string, name: string) => req<Profile>('GET', `${w(id)}/datasets/${name}/profile`),
  rows: (id: string, name: string, offset = 0, limit = 100) => req<{ total: number; rows: Record<string, string>[] }>('GET', `${w(id)}/datasets/${name}/rows?offset=${offset}&limit=${limit}`),
  relationships: (id: string) => req<Relationship[]>('GET', `${w(id)}/relationships`),
  program: (id: string, v: number) => req<Program>('GET', `${w(id)}/programs/${v}`),
  saveProgram: (id: string, code: string) => req<Program>('POST', `${w(id)}/programs`, { code }),
  run: (id: string) => req<{ run_id: string; status: string }>('POST', `${w(id)}/run`, {}),
  cancel: (id: string) => req('POST', `${w(id)}/cancel`),
  runs: (id: string) => req<{ id: string; program_version: number; status: string; started: number }[]>('GET', `${w(id)}/runs`),
  getRun: (id: string, rid: string) => req<Run & { validation: Validation | null }>('GET', `${w(id)}/runs/${rid}`),
  trace: (id: string, b: { run_id: string; table_id: string; level: string; soc: string | null; pt: string | null; group: string }) => req<Trace>('POST', `${w(id)}/trace`, b),
  userSpec: (id: string, spec: Spec, rationale: string) => req('POST', `${w(id)}/specs`, { spec, rationale }),
  decide: (id: string, v: number, decision: 'approve' | 'reject', note = '') => req('POST', `${w(id)}/specs/${v}/decision`, { decision, note }),
  message: (id: string, text: string, context?: Record<string, unknown>) => req('POST', `${w(id)}/messages`, { text, context }),
  signoff: (id: string, b: { run_id: string; reviewer: string; decision: string; note: string }) => req('POST', `${w(id)}/signoff`, b),
  exportUrl: (id: string, runId?: string) => `${w(id)}/export${runId ? `?run_id=${runId}` : ''}`,
  eventsUrl: (id: string, after = 0) => `${w(id)}/events?after=${after}`,
}
