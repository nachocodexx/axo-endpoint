import { CHART_COLORS } from 'axo-ui-kit/app/utils/status'

// Jobs as the VEM keeps them: a job = one id; each try is an attempt.

export type JobStatus = 'QUEUED' | 'RUNNING' | 'WAITING_RETRY' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
export const ACTIVE_STATUSES: JobStatus[] = ['QUEUED', 'RUNNING', 'WAITING_RETRY']
export const FINAL_STATUSES: JobStatus[] = ['COMPLETED', 'FAILED', 'CANCELLED']

export interface JobError {
  name?: string | null
  code?: number | null
  message?: string | null
  part?: string | null
  error_type?: string | null
  /** code (billed) / platform / queue (not billed) / cancelled (billed for the time it ran) */
  fault?: 'code' | 'platform' | 'queue' | 'cancelled' | null
}

export interface JobChoreography {
  choreography_id: string
  choreography_name?: string | null
  run_id: string
  step: string
  version?: number | null
  run_started_at?: string | null
  /** A run another choreography's step called: the outermost run, and that run's step. */
  root_run_id?: string | null
  root_step?: string | null
  parent?: { run_id: string; choreography_id: string; choreography_name: string; step: string } | null
}

export interface Job {
  job_id: string
  function_id: string
  function_version?: number | null
  function_name?: string | null
  status: JobStatus
  kind: 'function' | 'object' | string
  object_name?: string | null
  object_method?: string | null
  params?: Record<string, unknown> | null
  endpoint_id?: string | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  attempt: number
  max_attempts: number
  next_attempt_at?: string | null
  outcome?: string | null
  error?: JobError | null
  started_by_type?: 'user' | 'api_key' | 'choreography' | 'trigger' | 'policy' | string | null
  started_by_id?: string | null
  started_by_name?: string | null
  acting_user_id?: string | null
  choreography?: JobChoreography | null
  keep_result?: string | null
  keep_until?: string | null
  result?: { stored?: boolean; reason?: string; size?: number; name?: string; saved_at?: string; deleted_at?: string | null } | null
  cancelled_by?: string | null
  retried_by?: string | null
  warnings?: string[]
}

export interface FileWritten { bucket: string; name: string; version?: number | null; size?: number; format?: string | null }

export interface JobAttempt {
  job_id: string
  attempt: number
  outcome: string
  reason?: 'first' | 'platform_failed' | 'code_failed' | 'manual' | string
  retried_by?: string | null
  endpoint_id?: string | null
  received_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  layer_waits_ms?: Record<string, number>
  execution_ms?: number
  bytes_read?: Record<string, number>
  bytes_written?: Record<string, number>
  cache_hits?: number
  cache_misses?: number
  failure?: { error_class?: string; error_code?: number | null; component?: string; message?: string;
    error_type?: string | null; traceback?: string | null } | null
  retry_at?: string | null
  files_written?: FileWritten[]
}

export interface JobDetail extends Job { attempts: JobAttempt[] }

export interface RunSummary {
  run_id: string
  choreography_id?: string
  choreography_name?: string | null
  version?: number | null
  status?: string
  started_by_type?: string | null
  started_by_id?: string | null
  started_by_name?: string | null
  queued_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  steps_total?: number
}

export type JobListRow =
  | { type: 'job'; row_id: string; row_at: string; job: Job }
  | { type: 'run'; row_id: string; row_at: string; run: RunSummary; jobs: Job[]; shown: number; steps?: number | null }

export const ts = (iso?: string | null) => (iso ? Date.parse(iso) / 1000 : null)

export const isActive = (j: Pick<Job, 'status'>) => ACTIVE_STATUSES.includes(j.status)

/** "Running · attempt 2 of 3" (just the attempt part). */
export function attemptText(j: Pick<Job, 'attempt' | 'max_attempts'>): string | null {
  const most = Math.max(j.max_attempts ?? 1, j.attempt ?? 1)
  return most > 1 || (j.attempt ?? 1) > 1 ? `attempt ${j.attempt} of ${most}` : null
}

/** An object method as people read it: create / upgrade / restore for the platform's own steps. */
export function objectMethodTitle(method: string | null | undefined): string {
  return { __create__: 'create', __upgrade__: 'upgrade', __restore__: 'restore' }[method ?? ''] ?? method ?? 'run'
}

/** What the job is: a function (version) or an object's method ("cart-42 · add_item"). */
export function jobSubject(j: Job): string {
  if (j.kind === 'object') return `${j.object_name ?? 'object'} · ${objectMethodTitle(j.object_method)}`
  return j.function_name ?? j.function_id
}

export function startedByText(j: Pick<Job, 'started_by_type' | 'started_by_name' | 'started_by_id' | 'choreography'>): string {
  const who = j.started_by_name || j.started_by_id || '—'
  switch (j.started_by_type) {
    case 'api_key': return `API key ${who}`
    case 'choreography': return `${j.choreography?.choreography_name ?? who} · ${j.choreography?.step ?? 'step'}`
    case 'trigger': return `File in ${who}`
    case 'policy': return `Policy ${who}`
    default: return who
  }
}

export const STARTED_BY_ICONS: Record<string, string> = {
  user: 'mdi-account-outline', api_key: 'mdi-key-outline', choreography: 'mdi-sitemap-outline',
  trigger: 'mdi-file-upload-outline', policy: 'mdi-auto-fix',
}

/** The plain words for who caused a failure, and whether it's billed. */
export const FAULTS: Record<string, { label: string; text: string; color: string }> = {
  code: { label: 'Your code failed', text: 'Billed: the function ran.', color: 'error' },
  platform: { label: 'The platform failed', text: 'Not billed; it is tried again on its own (up to 3 tries).', color: 'warning' },
  queue: { label: 'Waited too long in the queue', text: 'Not billed: it never ran.', color: 'warning' },
  cancelled: { label: 'Cancelled', text: 'Billed for the time it ran.', color: 'muted' },
}

const OUTCOME_FAULT: Record<string, string> = {
  failed_user: 'code', failed_platform: 'platform', timed_out_in_queue: 'queue', cancelled: 'cancelled',
}
export const faultOf = (outcome?: string | null) => (outcome ? OUTCOME_FAULT[outcome] ?? null : null)

export const ATTEMPT_REASONS: Record<string, string> = {
  first: 'First try',
  platform_failed: 'Tried again: the platform failed',
  code_failed: 'Tried again: the code failed',
  manual: 'Retried by hand',
}

/** The layers a job waits in, in order, with their colours (the time bar). */
export const LAYERS: Array<{ key: string; label: string; color: string; text: string }> = [
  { key: 'control_plane', label: 'Front door', color: CHART_COLORS.grey, text: 'The Control Plane checked and passed the request on (a sleeping VE wakes here).' },
  { key: 'vem', label: 'VE', color: CHART_COLORS.violet, text: 'The VE picked an endpoint (or woke one).' },
  { key: 'endpoint', label: 'Endpoint queue', color: CHART_COLORS.orange, text: 'It waited on the endpoint for a free worker.' },
  { key: 'runner', label: 'Runner', color: CHART_COLORS.cyan, text: 'Its runner started (a cold start) or was busy.' },
  { key: 'execution', label: 'Running', color: CHART_COLORS.blue, text: 'Your code ran.' },
]

export function attemptSegments(a: JobAttempt): Array<{ key: string; label: string; color: string; ms: number; text: string }> {
  const waits = a.layer_waits_ms ?? {}
  const known = new Set(LAYERS.map(l => l.key))
  const out = LAYERS.map(l => ({ ...l, ms: l.key === 'execution' ? a.execution_ms ?? 0 : waits[l.key] ?? 0 }))
  for (const [k, ms] of Object.entries(waits)) {
    if (!known.has(k)) out.splice(out.length - 1, 0, { key: k, label: k, color: CHART_COLORS.teal, ms, text: '' })
  }
  return out.filter(s => s.ms > 0)
}

/** How long a result is kept: the choices (the VEM's KEEP_RESULT_CHOICES). */
export const KEEP_CHOICES: Array<{ value: string; title: string }> = [
  { value: '1h', title: '1 hour' }, { value: '1d', title: '1 day' }, { value: '7d', title: '7 days' },
  { value: '30d', title: '30 days' }, { value: '90d', title: '90 days' }, { value: '1y', title: '1 year' },
  { value: 'never', title: 'Never deleted' },
]
const KEEP_SECONDS: Record<string, number> = { '1h': 3600, '1d': 86400, '7d': 604800, '30d': 2592000, '90d': 7776000, '1y': 31536000 }
export const keepTitle = (v?: string | null) => KEEP_CHOICES.find(k => k.value === v)?.title ?? v ?? '—'
/** Choices no longer than `longest`. */
export const keepUpTo = (longest?: string | null) => KEEP_CHOICES.filter(
  k => !longest || longest === 'never' || (k.value !== 'never' && KEEP_SECONDS[k.value]! <= KEEP_SECONDS[longest]!))

export const STATUS_FILTERS = [
  { value: 'QUEUED', title: 'Queued' }, { value: 'RUNNING', title: 'Running' },
  { value: 'WAITING_RETRY', title: 'Waiting to retry' }, { value: 'COMPLETED', title: 'Completed' },
  { value: 'FAILED', title: 'Failed' }, { value: 'CANCELLED', title: 'Cancelled' },
]
export const FAULT_FILTERS = [
  { value: 'failed_user', title: 'Your code failed' }, { value: 'failed_platform', title: 'The platform failed' },
  { value: 'timed_out_in_queue', title: 'Waited too long' }, { value: 'cancelled', title: 'Cancelled' },
]

/** The JSON a result is shown as (at most `max` characters; longer = cut). */
export function prettyJson(value: unknown, max = 200_000): { text: string; cut: boolean; size: number } {
  const text = JSON.stringify(value, null, 2) ?? 'null'
  return { text: text.length > max ? text.slice(0, max) : text, cut: text.length > max, size: new Blob([text]).size }
}

/** The SDK lines that run this function (version) with these values. */
export function sdkSnippet(fn: { function_id: string; version: number; name: string }, params: Record<string, unknown>,
  ve: string): string {
  const args = Object.entries(params).map(([k, v]) => `, ${k}=${pyValue(v)}`).join('')
  return `from axo import AxoClient, FunctionRef

client = AxoClient(ve="${ve}")   # the API key comes from AXO_API_KEY
fn = FunctionRef(function_id="${fn.function_id}", version=${fn.version}, name="${fn.name}")
job = client.call(fn${args}).unwrap()
print(job.result().unwrap())
`
}

function pyValue(v: unknown): string {
  if (v === null || v === undefined) return 'None'
  if (v === true) return 'True'
  if (v === false) return 'False'
  if (typeof v === 'string') return JSON.stringify(v)
  if (typeof v === 'number') return String(v)
  return JSON.stringify(v).replace(/\btrue\b/g, 'True').replace(/\bfalse\b/g, 'False').replace(/\bnull\b/g, 'None')
}
