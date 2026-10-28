// Functions as the VEM lists them (GET /functions): one row per version.
// The pages show one row per function (its newest version) and its
// versions inside.

export type ParamType = 'number' | 'string' | 'boolean' | 'json' | 'data_ref'

export interface ParamSpec {
  name: string
  type: ParamType | string
  required: boolean
  default?: unknown
  description?: string | null
}

export interface RuntimeSpec {
  type: 'process' | 'container' | string
  call_style?: string
  python_version?: string | null
  requirements?: string[]
  max_concurrency?: number
  idle_ttl_seconds?: number | null
  cpu_limit?: number | null
  memory_limit_bytes?: number | null
  max_attempts?: number
  retry_delay_seconds?: number
  retry_backoff?: 'fixed' | 'exponential'
  keep_result?: string | null
  /** Values are null for secrets (never shown) and, for Viewers, for all. */
  env_vars?: Record<string, string | null>
  secret_env?: string[]
}

export interface FunctionVersion {
  function_id: string
  version: number
  name: string
  description?: string | null
  runtime_spec?: RuntimeSpec | null
  params_schema?: ParamSpec[] | null
  code_format?: 'source' | 'cloudpickle' | string
  code_hash?: string
  endpoint_id?: string[]
  container_status?: string | null
  deleted_at?: string | null
  registered_by?: string | null
  registered_by_name?: string | null
  registered_at?: string | null
  has_display_source?: boolean
  /** The version that runs when none is named (the newest unless someone picked another). */
  is_default?: boolean
}

export interface FunctionCode {
  function_id: string
  version: number
  name: string
  code_format: string
  code_hash: string
  text: string | null
  downloadable: boolean
  runtime_spec?: RuntimeSpec | null
  registered_by?: string | null
  registered_by_name?: string | null
  registered_at?: string | null
}

export interface FunctionRow {
  function_id: string
  name: string
  latest: FunctionVersion
  /** The version that runs when none is named. */
  defaultVersion: number
  versions: FunctionVersion[]
  deleted: boolean
}

/** One row per function: its newest live version (or newest at all, deleted). */
export function groupFunctions(all: FunctionVersion[]): FunctionRow[] {
  const by = new Map<string, FunctionVersion[]>()
  for (const v of all) by.set(v.function_id, [...(by.get(v.function_id) ?? []), v])
  return [...by.entries()].map(([function_id, versions]) => {
    const sorted = [...versions].sort((a, b) => b.version - a.version)
    const live = sorted.filter(v => !v.deleted_at)
    const latest = live[0] ?? sorted[0]!
    const defaultVersion = live.find(v => v.is_default)?.version ?? latest.version
    return { function_id, name: latest.name, latest, defaultVersion, versions: sorted, deleted: live.length === 0 }
  }).sort((a, b) => a.name.localeCompare(b.name))
}

/** "Simple" (a process on the endpoint) or "Container · Python 3.12". */
export function runtimeLabel(spec?: RuntimeSpec | null): string {
  if (spec?.type === 'container') return `Container · Python ${spec.python_version ?? '—'}`
  return 'Simple'
}

/** Ready / starting / stopped / crashed / deleted (the status chip). */
export function functionState(v: FunctionVersion, deleted = false): string {
  if (deleted || v.deleted_at) return 'deleted'
  if (v.runtime_spec?.type === 'container') {
    if (v.container_status === 'running') return 'ready'
    if (v.container_status === 'crashed') return 'crashed'
    return v.container_status === 'stopped' ? 'stopped' : 'ready'
  }
  return 'ready'
}

export const PYTHON_VERSIONS = ['3.10', '3.11', '3.12', '3.13']

/** The starter of "New function": named parameters, type hints, a docstring. */
export const STARTER_CODE = `def resize(width: int, height: int = 100, keep_ratio: bool = True, ctx=None):
    """Works out the new size of an image.

    Args:
        width: The new width, in pixels.
        height: The new height, in pixels.
        keep_ratio: Keep the shape of the image.
    """
    if keep_ratio:
        height = round(width * 0.75)
    return {"width": width, "height": height}
`

/** A Python identifier (a function's name). */
export const isIdentifier = (s: string) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(s)
