// One endpoint as the VEM lists it (GET /endpoints): what the VEM manages
// (lifecycle, size) plus what the endpoint itself last reported (metrics).

export interface EndpointMetrics {
  submitted: number
  completed: number
  failed: number
  rejected: number
  queue_depth: number
  queue_capacity: number
  consensus_role: 'leader' | 'follower' | string
  consensus_term: number
  consensus_leader_ids: string[]
  draining: boolean
  /** `function_id::version` -> runner containers (live, idle). */
  container_pools: Record<string, { live: number; idle: number }>
}

export interface EndpointRow {
  endpoint_id: string
  lifecycle?: string | null
  lifecycle_since?: string | null
  lifecycle_reason?: string | null
  cpu_cores?: number | null
  memory_mb?: number | null
  failures?: number
  status?: string | null
  created_at?: string | null
  uptime_ms?: number | null
  router_bind?: string | null
  pub_bind?: string | null
  purge_eligible?: boolean
  unreachable_since?: string | null
  metrics?: EndpointMetrics
}

/** One live sample of an endpoint's container (the VEM's stats feed). */
export interface EndpointStats {
  endpoint_id: string
  available: boolean
  cpu_percent?: number
  memory_usage?: number
  memory_limit?: number
  network_rx?: number
  network_tx?: number
}

/** The state to show: the VEM's lifecycle, else what the endpoint reported. */
export function endpointState(e: EndpointRow): string {
  const raw = e.lifecycle ?? (e.status === 'unreachable' ? 'unreachable' : e.status) ?? ''
  return raw ? raw[0]!.toUpperCase() + raw.slice(1) : 'Unknown'
}

/** Leads now (a gone endpoint's last report may still say leader). */
export const isLeader = (e: EndpointRow) => endpointState(e) === 'Running' && e.metrics?.consensus_role === 'leader'
/** Still counts toward the VE (it exists or is about to). */
export const isPresent = (e: EndpointRow) => ['Creating', 'Starting', 'Running', 'Sleeping'].includes(endpointState(e))
export const isGone = (e: EndpointRow) => ['Removed', 'Stopped', 'Unreachable'].includes(endpointState(e))

/** CPU in % of the endpoint's own size (Docker counts 100 % per core). */
export function cpuOfSize(stats: EndpointStats | undefined, cores: number | null | undefined): number | null {
  if (!stats?.available || stats.cpu_percent == null) return null
  return stats.cpu_percent / Math.max(cores || 1, 0.01)
}

export function memoryOfSize(stats: EndpointStats | undefined): number | null {
  if (!stats?.available || !stats.memory_limit) return null
  return (100 * (stats.memory_usage ?? 0)) / stats.memory_limit
}

/** `function_id::version` -> [function_id, version]. */
export function splitPoolKey(key: string): [string, string] {
  const at = key.lastIndexOf('::')
  return at < 0 ? [key, ''] : [key.slice(0, at), key.slice(at + 2)]
}
