import dagre from '@dagrejs/dagre'

// Choreographies as the VEM keeps them (GET /choreographies...): a graph of
// steps (a function, an object's method, another choreography, or a
// bucket), arrows between them and the triggers that start a run; every save
// is a new version. A run keeps each step's state and which arrows were
// followed; a choreography step starts a run of its own, linked to this one.

export type NodeKind = 'function' | 'object' | 'choreography' | 'bucket'

export interface ParamMerge {
  strategy: 'priority' | 'from' | 'random' | 'first_finished' | 'last_finished' | 'collect' | 'reduce' | 'none'
  order?: string[] | null
  source?: string | null
  reducer?: 'sum' | 'min' | 'max' | 'concat' | 'all_equal' | null
  as_list?: boolean
}

export interface ConditionRule { field: string; op: string; value?: unknown }
export interface Condition { match: 'all' | 'any'; rules: Array<ConditionRule | Condition> }
export const isGroup = (r: ConditionRule | Condition): r is Condition => Array.isArray((r as Condition).rules)

export interface ChNode {
  node_id: string
  kind: NodeKind
  label?: string | null
  position?: { x?: number; y?: number }
  function_id?: string | null
  function_version?: number | null
  function_name?: string | null
  object_name?: string | null
  method?: string | null
  /** A choreography step: the one it runs (by id), pinned version or the newest. */
  choreography_id?: string | null
  choreography_version?: number | null
  choreography_name?: string | null
  params?: Record<string, unknown>
  merge?: Record<string, ParamMerge>
  join?: 'all' | 'arrived'
  max_attempts?: number | null
  retry_delay_seconds?: number | null
  retry_backoff?: 'fixed' | 'exponential' | null
  timeout_seconds?: number | null
  max_wait?: number | null
  files_mode?: 'per_file' | 'all_at_once'
  parallelism?: number
  bucket_name?: string | null
  key_pattern?: string | null
  selected_items?: Array<{ name: string; version?: number | null }> | null
  save_result?: boolean
}

export interface ChEdge {
  edge_id: string
  source_node_id: string
  target_node_id: string
  condition?: Condition | null
  otherwise?: boolean
  on_failure?: boolean
}

export interface ChTrigger {
  trigger_id?: string
  event_type: string
  bucket?: string | null
  prefix?: string | null
  condition?: Condition | null
  targets?: string[]
  created_by?: string | null
  created_at?: string | null
}

export interface ChGraph { nodes: ChNode[]; edges: ChEdge[]; triggers?: ChTrigger[] }

export interface ChSettings {
  max_parallel_runs: number
  queue_limit: number
  allow_completed_with_errors: boolean
  keep_results: string | null
  /** With several end steps: the one whose answer is the run's answer (when a choreography step calls it). */
  answer_step?: string | null
}

export interface StepParamInfo { name: string; required: boolean; default?: unknown; value?: unknown; merge?: ParamMerge | null; senders?: string[] }
export interface StepInfo { senders: string[]; files_from: string | null; exists: boolean; params: StepParamInfo[]; merge_all?: ParamMerge | null }

export interface RunSummary {
  run_id: string
  status: string
  version: number
  started_by_type?: string
  started_by_id?: string | null
  started_by_name?: string | null
  trigger?: Record<string, any> | null
  parent?: RunParent | null
  counts?: Record<string, number>
  queued_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
}

/** A row of GET /choreographies. */
export interface ChoreographyRow {
  choreography_id: string
  name: string
  description?: string | null
  version: number
  steps: number
  triggers: number
  owner_user_id: string
  updated_at?: string | null
  updated_by?: string | null
  updated_by_name?: string | null
  last_run?: RunSummary | null
  runs_24h: number
  errors_24h: number
  active_runs: number
  /** The choreographies its steps call (ids). */
  uses?: string[]
}

/** A step calling another choreography (detail's `uses`). */
export interface ChUse { step: string; choreography_id: string; name: string; version?: number | null; latest_version?: number | null; exists: boolean }
/** A choreography whose newest version calls this one (detail's `used_by`). */
export interface ChUsedBy { choreography_id: string; name: string; version: number; steps: string[]; pinned: number[] }
/** The run that started this one (a choreography step of it). */
export interface RunParent { run_id: string; choreography_id: string; choreography_name: string; version: number; step: string; item?: number | null }
/** A choreography step's own run, as the step keeps it. */
export interface ChildRun {
  run_id: string
  choreography_id: string
  choreography_name: string
  version: number
  status: string
  done?: number
  total?: number
  /** Its place in the called choreography's line, while it waits. */
  position?: number
  answer_job_id?: string | null
}

/** GET /choreographies/{ref} (a version with ?version=). */
export interface Choreography {
  choreography_id: string
  name: string
  description?: string | null
  owner_user_id: string
  version: number
  latest_version: number
  graph: ChGraph
  settings: ChSettings
  triggers: ChTrigger[]
  created_at?: string | null
  updated_at?: string | null
  updated_by?: string | null
  updated_by_name?: string | null
  active_runs?: number
  warnings: string[]
  steps: Record<string, StepInfo>
  uses?: ChUse[]
  used_by?: ChUsedBy[]
  /** Its end steps (nothing after them). */
  ends?: string[]
}

/** A problem / warning the VEM wrote ("step train: ...", "trigger 2: ...",
 *  "connection e3 ..."), with the step / arrow / trigger it is about. */
export interface Problem { message: string; node_id?: string | null; edge_id?: string | null; trigger_id?: string | null; warning?: boolean }
export function placeProblems(messages: string[], graph: ChGraph, warning = false): Problem[] {
  const ids = new Set(graph.nodes.map(n => n.node_id))
  const triggers = graph.triggers ?? []
  return messages.map((message) => {
    const out: Problem = { message, warning }
    const step = /^steps? ([A-Za-z0-9_-]+)/.exec(message)?.[1] ?? /\(([A-Za-z0-9_-]+) →/.exec(message)?.[1]
    if (step && ids.has(step)) out.node_id = step
    const edge = /^connection (\S+)/.exec(message)?.[1]
    if (edge) out.edge_id = edge
    const trig = /^trigger (\S+?):?\s/.exec(message)?.[1]
    if (trig) {
      const t = triggers.find(x => x.trigger_id === trig) ?? triggers[Number(trig) - 1]
      out.trigger_id = t?.trigger_id ?? trig
    }
    return out
  })
}
export interface CheckAnswer { ok: boolean; problems: string[]; warnings: string[]; steps: Record<string, StepInfo> }

/** Where a step's parameter came from, in plain words. */
export function originText(origin: unknown, title: (id: string) => string = id => id): string {
  if (origin === 'step') return 'Set on the step'
  if (origin === 'data') return 'The bucket file'
  if (origin === 'default' || origin == null) return "The code's default"
  if (Array.isArray(origin)) return `From ${origin.map(o => title(String(o))).join(', ')}`
  if (typeof origin === 'object') return `From ${Object.keys(origin as object).map(title).join(', ')}`
  return `From ${title(String(origin))}`
}

export interface ItemState {
  index: number
  file: { bucket?: string; name: string; version?: number | null }
  status: string
  attempt: number
  job_id?: string | null
  error?: string | null
  error_name?: string | null
  duration_ms?: number | null
  child?: ChildRun | null
}

export interface StepState {
  node_id: string
  kind: NodeKind
  status: string
  attempt: number
  target_version?: number | null
  job_id?: string | null
  endpoint_id?: string | null
  params: Record<string, unknown>
  param_origins: Record<string, unknown>
  output_bytes: number
  files: Array<{ bucket: string; name: string; version?: number | null; size?: number; format?: string; written_by?: string }>
  items: ItemState[]
  error?: string | null
  error_name?: string | null
  warnings: string[]
  child?: ChildRun | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
}

export interface Run {
  run_id: string
  choreography_id: string
  choreography_name: string
  version: number
  status: string
  acting_user_id?: string | null
  started_by_type: 'user' | 'trigger' | string
  started_by_id?: string | null
  started_by_name?: string | null
  trigger?: Record<string, any> | null
  input?: unknown
  files?: Record<string, unknown>
  steps: Record<string, StepState>
  counts?: Record<string, number>
  result?: Record<string, unknown>
  resumes: number
  stopped_by?: string | null
  parent?: RunParent | null
  chain?: string[]
  versions?: Record<string, number>
  edge_states: Record<string, 'taken' | 'not_taken' | string>
  queued_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
}

export interface DesignerSetting {
  name: string
  type: string
  description: string
  default?: unknown
  kinds?: NodeKind[]
  choices?: string[]
  min?: number
  max?: number
  for?: string[]
  placeholder?: string
}
export interface MergeStrategyInfo { name: ParamMerge['strategy']; label: string; same_every_run: boolean; options: string[]; description: string; reducers?: Array<{ name: string; description: string }> }
export interface ConditionOperator { op: string; label: string; value: string | null; description?: string }
export interface EventField { name: string; type: string; description?: string }
export interface EventType {
  type: string
  built_in?: boolean
  description?: string | null
  fields: EventField[]
  used_by?: Array<{ choreography: string; choreography_id: string; trigger_id?: string }>
  count?: number
  last_seen?: string | null
  found?: boolean
}
export interface DesignerInfo {
  step_settings: DesignerSetting[]
  arrow_settings: DesignerSetting[]
  trigger_settings: DesignerSetting[]
  choreography_settings: DesignerSetting[]
  merge_strategies: MergeStrategyInfo[]
  condition_operators: ConditionOperator[]
  event_types: EventType[]
}

export interface ChEvent {
  id: string
  type: string
  source?: string | null
  subject?: string | null
  time: string
  data?: unknown
  sent_by?: string | null
  sent_by_type?: string | null
  sent_by_name?: string | null
  runs?: Array<{ choreography_id: string; choreography: string; run_id: string; trigger_id?: string }>
  duplicate?: boolean
}

export interface Suggestions {
  event_types: Array<{ type: string; count: number; used_in: string[] }>
  conditions: Array<{ where: 'arrow' | 'trigger'; event_type?: string | null; condition: Condition; count: number; used_in: string[] }>
}

export interface Visibility {
  kind: 'function' | 'choreography' | 'object'
  ref: string
  mode: 'runner' | 've' | 'teams'
  teams: string[]
  set_by?: string | null
  set_at?: string | null
  creator?: string | null
  can_change: boolean
}

// ── statuses ─────────────────────────────────────────────────────────────────
export interface StepLook { color: string; icon: string; label: string; ring?: boolean }
export const STEP_LOOKS: Record<string, StepLook> = {
  pending: { color: 'muted', icon: 'mdi-circle-outline', label: 'Waiting' },
  queued: { color: 'info', icon: 'mdi-tray-full', label: 'Queued' },
  running: { color: 'info', icon: 'mdi-play-circle-outline', label: 'Running', ring: true },
  completed: { color: 'success', icon: 'mdi-check-circle', label: 'Completed' },
  completed_with_errors: { color: 'warning', icon: 'mdi-alert-circle-check', label: 'Completed with errors' },
  failed: { color: 'error', icon: 'mdi-close-circle', label: 'Failed' },
  skipped: { color: 'muted', icon: 'mdi-debug-step-over', label: 'Skipped' },
  cancelled: { color: 'muted', icon: 'mdi-cancel', label: 'Cancelled' },
  not_taken: { color: 'muted', icon: 'mdi-call-split', label: 'Not taken' },
}
export const stepLook = (s?: string | null): StepLook => STEP_LOOKS[s ?? ''] ?? STEP_LOOKS.pending!
export const RUN_ACTIVE = ['queued', 'running']
export const RUN_DONE = ['completed', 'completed_with_errors', 'failed', 'cancelled']
export const runActive = (s?: string | null) => RUN_ACTIVE.includes(s ?? '')
export const runFailed = (s?: string | null) => s === 'failed' || s === 'completed_with_errors'
export const RUN_STATUS_CHOICES = Object.entries(STEP_LOOKS).filter(([k]) => ['queued', 'running', ...RUN_DONE].includes(k))
  .map(([value, l]) => ({ value, title: l.label }))

// ── nodes ────────────────────────────────────────────────────────────────────
export const KIND_INFO: Record<NodeKind, { title: string; icon: string; color: string }> = {
  function: { title: 'Function', icon: 'mdi-function-variant', color: 'primary' },
  object: { title: 'Object', icon: 'mdi-shape-outline', color: 'info' },
  choreography: { title: 'Choreography', icon: 'mdi-layers-triple-outline', color: 'success' },
  bucket: { title: 'Bucket', icon: 'mdi-bucket-outline', color: 'brand-3' },
}

/** What a bucket step does here: reads files (nothing before it), saves
 *  results (nothing after it), or saves then passes this run's files on. */
export type BucketRole = 'source' | 'store' | 'store_pass'
export function bucketRole(graph: ChGraph, nodeId: string): BucketRole {
  const into = graph.edges.some(e => e.target_node_id === nodeId)
  const out = graph.edges.some(e => e.source_node_id === nodeId)
  return !into ? 'source' : out ? 'store_pass' : 'store'
}
export const BUCKET_ROLE_TEXT: Record<BucketRole, string> = {
  source: 'Reads files',
  store: 'Saves results',
  store_pass: 'Saves, then passes on',
}

export function nodeTitle(n: ChNode, fname: (id: string) => string = id => id): string {
  if (n.label) return n.label
  if (n.kind === 'function') return n.function_name ?? (n.function_id ? fname(n.function_id) : 'Function')
  if (n.kind === 'object') return `${n.object_name ?? 'object'}.${n.method || 'run'}`
  if (n.kind === 'choreography') return n.choreography_name ?? 'Choreography'
  return n.bucket_name ?? 'Bucket'
}
export function nodeSubtitle(n: ChNode, fname: (id: string) => string = id => id, graph?: ChGraph,
  chInfo?: (id: string) => { version: number; steps: number } | null | undefined): string {
  if (n.kind === 'choreography') {
    const c = n.choreography_id ? chInfo?.(n.choreography_id) : null
    const v = n.choreography_version ? `v${n.choreography_version}` : c ? `newest (v${c.version})` : 'newest'
    return [n.label ? n.choreography_name : null, v, c ? `${c.steps} step${c.steps === 1 ? '' : 's'}` : null].filter(Boolean).join(' · ')
  }
  if (n.kind === 'function') return `${n.function_id ? fname(n.function_id) : '—'}${n.function_version ? ` v${n.function_version}` : ''}`
  if (n.kind === 'object') return `${n.object_name ?? '—'} · ${n.method || 'run'}`
  return `${n.bucket_name ?? '—'}${graph ? ` · ${BUCKET_ROLE_TEXT[bucketRole(graph, n.node_id)]}` : ''}`
}

/** A free step id like "train", "train-2" (the VEM wants [A-Za-z0-9_-]{1,40}). */
export function newNodeId(graph: ChGraph, base: string): string {
  const stem = (base || 'step').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32) || 'step'
  const used = new Set(graph.nodes.map(n => n.node_id))
  if (!used.has(stem)) return stem
  for (let i = 2; ; i++) if (!used.has(`${stem}-${i}`)) return `${stem}-${i}`
}
export function newEdgeId(graph: ChGraph): string {
  const used = new Set(graph.edges.map(e => e.edge_id))
  for (let i = graph.edges.length; ; i++) if (!used.has(`e${i}`)) return `e${i}`
}
export const TRIGGER_NODE = (t: ChTrigger, i: number) => `trigger:${t.trigger_id || i}`

// ── conditions in plain words ────────────────────────────────────────────────
const OP_WORDS: Record<string, string> = {
  '==': 'is', '!=': 'is not', '>': '>', '>=': '≥', '<': '<', '<=': '≤', in: 'is one of', not_in: 'is none of',
  contains: 'contains', not_contains: "doesn't contain", exists: 'exists', not_exists: "doesn't exist",
  is_true: 'is true', is_false: 'is false', is_empty: 'is empty', not_empty: "isn't empty",
}
export const NO_VALUE_OPS = ['exists', 'not_exists', 'is_true', 'is_false', 'is_empty', 'not_empty']
const shown = (v: unknown) => (typeof v === 'string' ? `"${v}"` : JSON.stringify(v))
export function ruleText(r: ConditionRule): string {
  const field = r.field || 'the result'
  return NO_VALUE_OPS.includes(r.op) ? `${field} ${OP_WORDS[r.op] ?? r.op}` : `${field} ${OP_WORDS[r.op] ?? r.op} ${shown(r.value)}`
}
export function conditionText(c?: Condition | null, depth = 0): string {
  if (!c?.rules?.length) return ''
  const parts = c.rules.map(r => (isGroup(r) ? `(${conditionText(r, depth + 1)})` : ruleText(r)))
  return parts.join(c.match === 'any' ? ' or ' : ' and ')
}
export function edgeText(e: ChEdge): string {
  if (e.on_failure) return 'on failure'
  if (e.otherwise) return 'otherwise'
  return conditionText(e.condition)
}
/** Field paths of a JSON value ("a", "a.b", "items.0"), for the field pickers. */
export function fieldPaths(value: unknown, prefix = '', depth = 0, out: string[] = []): string[] {
  if (depth > 3 || value === null || typeof value !== 'object') return out
  const entries = Array.isArray(value) ? value.slice(0, 3).map((v, i) => [String(i), v] as const) : Object.entries(value as object)
  for (const [k, v] of entries) {
    const path = prefix ? `${prefix}.${k}` : k
    out.push(path)
    fieldPaths(v, path, depth + 1, out)
  }
  return out
}

/** Does a value (a step's last result, an event's data) match the rules?
 *  The same comparisons the VEM makes, for the dialogs' preview. */
export function evaluate(c: Condition | null | undefined, data: unknown): boolean | null {
  if (!c?.rules?.length) return null
  const one = (r: ConditionRule | Condition): boolean => (isGroup(r) ? !!evaluate(r, data) : ruleMatches(r, data))
  return c.match === 'any' ? c.rules.some(one) : c.rules.every(one)
}
function pick(data: unknown, path: string): { found: boolean; value: unknown } {
  if (!path) return { found: true, value: data }
  let cur: any = data
  for (const part of path.split('.')) {
    if (cur === null || typeof cur !== 'object' || !(part in cur)) return { found: false, value: undefined }
    cur = cur[part]
  }
  return { found: true, value: cur }
}
const truthy = (v: unknown) => !(v === false || v === 0 || v === '' || v === null || v === undefined
  || (Array.isArray(v) && !v.length) || (typeof v === 'object' && v !== null && !Array.isArray(v) && !Object.keys(v).length))
const empty = (v: unknown) => v === null || v === '' || (Array.isArray(v) && !v.length) || (typeof v === 'object' && v !== null && !Object.keys(v).length)
function ruleMatches(r: ConditionRule, data: unknown): boolean {
  const { found, value } = pick(data, r.field)
  const want = r.value
  const cmp = (f: (a: any, b: any) => boolean) => found && typeof value === typeof want && (typeof value === 'number' || typeof value === 'string') && f(value, want)
  switch (r.op) {
    case 'exists': return found
    case 'not_exists': return !found
    case 'is_true': return found && truthy(value)
    case 'is_false': return !found || !truthy(value)
    case 'is_empty': return !found || empty(value)
    case 'not_empty': return found && !empty(value)
    case '==': return found && JSON.stringify(value) === JSON.stringify(want)
    case '!=': return !found || JSON.stringify(value) !== JSON.stringify(want)
    case '>': return cmp((a, b) => a > b)
    case '>=': return cmp((a, b) => a >= b)
    case '<': return cmp((a, b) => a < b)
    case '<=': return cmp((a, b) => a <= b)
    case 'in': return found && Array.isArray(want) && want.some(w => JSON.stringify(w) === JSON.stringify(value))
    case 'not_in': return !found || !Array.isArray(want) || !want.some(w => JSON.stringify(w) === JSON.stringify(value))
    case 'contains': return found && contains(value, want)
    case 'not_contains': return !found || !contains(value, want)
    default: return false
  }
}
function contains(value: unknown, want: unknown): boolean {
  if (Array.isArray(value)) return value.some(v => JSON.stringify(v) === JSON.stringify(want))
  if (typeof value === 'string') return typeof want === 'string' && value.includes(want)
  if (value && typeof value === 'object') return typeof want === 'string' && want in value
  return false
}

// ── layout ───────────────────────────────────────────────────────────────────
export const NODE_W = 220
export const NODE_H = 76
export const TRIGGER_SIZE = 56

/** Places every step (and trigger) left to right with dagre: for Arrange,
 *  and for a choreography saved from Python (its steps have no position). */
export function arrange(graph: ChGraph): ChGraph {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 36, ranksep: 90, marginx: 20, marginy: 20 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of graph.nodes) g.setNode(n.node_id, { width: NODE_W, height: NODE_H })
  ;(graph.triggers ?? []).forEach((t, i) => g.setNode(TRIGGER_NODE(t, i), { width: TRIGGER_SIZE, height: TRIGGER_SIZE }))
  for (const e of graph.edges) g.setEdge(e.source_node_id, e.target_node_id)
  ;(graph.triggers ?? []).forEach((t, i) => {
    for (const target of startTargets(graph, t)) g.setEdge(TRIGGER_NODE(t, i), target)
  })
  dagre.layout(g)
  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      const p = g.node(n.node_id)
      return { ...n, position: { x: Math.round(p.x - NODE_W / 2), y: Math.round(p.y - NODE_H / 2) } }
    }),
  }
}
/** Where the trigger node sits (not saved: next to the steps it starts). */
export function triggerPositions(graph: ChGraph): Record<string, { x: number; y: number }> {
  const out: Record<string, { x: number; y: number }> = {}
  const byId = new Map(graph.nodes.map(n => [n.node_id, n]))
  ;(graph.triggers ?? []).forEach((t, i) => {
    const targets = startTargets(graph, t).map(id => byId.get(id)).filter(Boolean) as ChNode[]
    const ys = targets.map(n => (n.position?.y ?? 0) + NODE_H / 2)
    const x = Math.min(...targets.map(n => n.position?.x ?? 0), 0) - 130
    const y = ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : i * 90
    out[TRIGGER_NODE(t, i)] = { x, y: y - TRIGGER_SIZE / 2 + i * 8 }
  })
  return out
}
export const needsLayout = (graph: ChGraph) => graph.nodes.some(n => n.position?.x === undefined || n.position?.y === undefined)
/** The first steps (no arrow comes in): what a trigger starts. */
export const firstSteps = (graph: ChGraph) => graph.nodes.filter(n => !graph.edges.some(e => e.target_node_id === n.node_id))
export function startTargets(graph: ChGraph, t: ChTrigger): string[] {
  const ids = new Set(graph.nodes.map(n => n.node_id))
  const named = (t.targets ?? []).filter(id => ids.has(id))
  if (named.length) return named
  if (t.event_type === 'file.written' && t.bucket)
    return firstSteps(graph).filter(n => n.kind === 'bucket' && n.bucket_name === t.bucket).map(n => n.node_id)
  return firstSteps(graph).map(n => n.node_id)
}

// ── comparing two versions ───────────────────────────────────────────────────
const strip = (n: ChNode) => { const { position: _p, ...rest } = n; return JSON.stringify(rest) }
export interface GraphDiff { added: string[]; removed: string[]; changed: string[]; edgesAdded: string[]; edgesRemoved: string[]; triggers: string[]
  /** Choreography steps that now run another choreography or version, in words. */ calls: string[] }
export function diffGraphs(a: ChGraph, b: ChGraph): GraphDiff {
  const an = new Map(a.nodes.map(n => [n.node_id, n]))
  const bn = new Map(b.nodes.map(n => [n.node_id, n]))
  const key = (e: ChEdge) => `${e.source_node_id}→${e.target_node_id}|${edgeText(e)}`
  const ae = new Set(a.edges.map(key))
  const be = new Set(b.edges.map(key))
  const tkey = (t: ChTrigger) => `${t.event_type} ${t.bucket ?? ''}${t.prefix ?? ''} ${conditionText(t.condition)}`
  const at = new Set((a.triggers ?? []).map(tkey))
  const bt = new Set((b.triggers ?? []).map(tkey))
  return {
    added: [...bn.keys()].filter(k => !an.has(k)),
    removed: [...an.keys()].filter(k => !bn.has(k)),
    changed: [...bn.keys()].filter(k => an.has(k) && strip(an.get(k)!) !== strip(bn.get(k)!)),
    edgesAdded: [...be].filter(k => !ae.has(k)).map(k => k.split('|')[0]!),
    edgesRemoved: [...ae].filter(k => !be.has(k)).map(k => k.split('|')[0]!),
    triggers: [...[...bt].filter(k => !at.has(k)).map(k => `+ ${k.trim()}`), ...[...at].filter(k => !bt.has(k)).map(k => `− ${k.trim()}`)],
    calls: [...bn.values()].filter(n => n.kind === 'choreography' && an.get(n.node_id)?.kind === 'choreography').flatMap((n) => {
      const o = an.get(n.node_id)!
      const v = (x: ChNode) => (x.choreography_version ? `v${x.choreography_version}` : 'the newest version')
      if (o.choreography_id !== n.choreography_id) return [`Step ${n.node_id} now runs ${n.choreography_name ?? n.choreography_id} (was ${o.choreography_name ?? o.choreography_id})`]
      if (o.choreography_version !== n.choreography_version) return [`Step ${n.node_id} now runs ${v(n)} of ${n.choreography_name ?? 'it'} (was ${v(o)})`]
      return []
    }),
  }
}

/** What's sent to save: positions rounded, empty optional fields dropped. */
export function cleanGraph(graph: ChGraph): ChGraph {
  return {
    nodes: graph.nodes.map(n => ({ ...n, position: { x: Math.round(n.position?.x ?? 0), y: Math.round(n.position?.y ?? 0) } })),
    edges: graph.edges.map(e => ({ ...e })),
    triggers: (graph.triggers ?? []).map(({ created_by: _c, created_at: _a, ...t }) => t),
  }
}

export const DEFAULT_SETTINGS: ChSettings = { max_parallel_runs: 10, queue_limit: 1000, allow_completed_with_errors: true, keep_results: null, answer_step: null }

/** The choreographies that would call `selfId` back (directly or deeper) --
 *  adding one of them as a step would make a loop -- plus itself. */
export function loopMakers(rows: Array<{ choreography_id: string; uses?: string[] }>, selfId: string | null): Set<string> {
  const out = new Set<string>()
  if (!selfId) return out
  out.add(selfId)
  const uses = new Map(rows.map(r => [r.choreography_id, r.uses ?? []]))
  const reaches = (from: string, seen = new Set<string>()): boolean => {
    if (from === selfId) return true
    if (seen.has(from)) return false
    seen.add(from)
    return (uses.get(from) ?? []).some(x => reaches(x, seen))
  }
  for (const r of rows) if (reaches(r.choreography_id)) out.add(r.choreography_id)
  return out
}

/** A called run's progress in a few words: "2 of 3 steps", "3rd in line". */
export function childText(c?: ChildRun | null): string {
  if (!c) return ''
  if (c.position) return `${ordinal(c.position)} in line`
  if (c.total) return `${c.done ?? 0} of ${c.total} steps`
  return stepLook(c.status).label
}
export function ordinal(n: number): string {
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'
  return `${n}${s}`
}
export const KEEP_CHOICES = [
  { value: '1h', title: '1 hour' }, { value: '1d', title: '1 day' }, { value: '7d', title: '7 days' }, { value: '30d', title: '30 days' },
  { value: '90d', title: '90 days' }, { value: '1y', title: '1 year' }, { value: 'never', title: "Don't keep" },
]
export const keepTitle = (v?: string | null) => KEEP_CHOICES.find(k => k.value === v)?.title ?? 'The VE default'

export const CHOREOGRAPHY_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/
export function choreographyNameProblem(name: string): string | undefined {
  if (!name) return 'A name is needed'
  if (!CHOREOGRAPHY_NAME_RE.test(name)) return 'Letters, digits, - _ . (up to 63), starting with a letter or digit'
}

export function emitSnippet(veId: string, type = 'order.paid'): string {
  return `from axo import AxoClient

client = AxoClient(ve="${veId}")
client.emit("${type}", {"order_id": "ord-1001", "total": 240.5}, id="ord-1001")`
}
