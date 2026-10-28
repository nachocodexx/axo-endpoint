// Shapes the Control Plane answers with (see axo_control_plane/infrastructure/api).

/** Every error body: name + number from the shared error catalog. */
export interface ApiErrorBody {
  error: string
  error_code: number
  message: string
  details?: Record<string, any>
}

/** One entry of GET /errors (axo_shared/error_catalog.py). */
export interface CatalogEntry {
  code: number
  name: string
  component: string
  description: string
  hint: string
  http_status: number | null
}

export interface ErrorCatalog {
  components: Record<string, string>
  errors: CatalogEntry[]
}

export interface Session {
  access_token: string
  temporal_secret: string
  username?: string
}

export interface Me {
  user_id: string
  username: string
  email?: string
  first_name?: string
  last_name?: string
  platform_role: string
  teams: Array<{ team_id: string; name: string; role: string }>
  unread_notifications: number
  via_key: string | null
  password_change_required?: boolean
}

export interface Notice {
  notification_id: string
  kind: string
  text: string
  /** `text` with `{name}` placeholders; the UI writes it in the person's settings. */
  template?: string
  fields?: Record<string, { type: string; value: any }>
  data: Record<string, any>
  read: boolean
  created_at: number
}

export type VeLifecycle =
  | 'Creating' | 'Running' | 'Scaling' | 'Stopping' | 'Stopped' | 'Starting'
  | 'Restoring' | 'Sleeping' | 'Waking' | 'Deleting' | 'Deleted' | 'Failed'
export type VeHealth = 'Healthy' | 'Degraded' | 'Unavailable' | null

export interface VeCard {
  ve_id: string
  name: string
  owner: { kind: 'user' | 'team'; id: string; name?: string }
  plan_id: string
  quota: { cpu_cores: number; memory_mb: number }
  endpoint_size: { name: string; cpu_cores: number; memory_mb: number }
  target_endpoints: number
  min_endpoints: number
  endpoints_running: number
  lifecycle: VeLifecycle
  health: VeHealth
  lifecycle_reason?: string | null
  desired: string
  created_by?: string
  created_at: number
  idle_since?: number | null
  heartbeat_at?: number | null
  my_role?: string | null
  rental: {
    period_start: number | null
    period_end: number | null
    period_fee_cents: number | null
    cancel_at_period_end: boolean
    pending_change: Record<string, any> | null
    ended_at: number | null
    delete_after: number | null
    budget_cents: number | null
  }
  /** A plan with a request limit (Free): this period's use. */
  requests?: { used: number; limit: number }
  /** /me/overview only: this period's bill so far (Owner / Admin). */
  current_total_cents?: number | null
  current_usage?: { attempts: number; billable_attempts: number; requests: number; total_ucents: number } | null
}

/** /stats/chart */
export interface ChartPoint { t: number; group: string | null; values: Record<string, number | null> }
export interface ChartAnswer {
  metric: string; start: number; end: number; step_s: number; bin_width_s: number
  group_by: string | null; points: ChartPoint[]
}

/** /stats/summary: the whole window as one value set per group. */
export interface SummaryAnswer {
  metric: string; start: number; end: number; group_by: string | null
  groups: Array<{ group: string | null; values: Record<string, number | null> }>
}

/** /stats/top: rows from the attempt records (cost only when `money`). */
export interface TopRow {
  function_id?: string; function_version?: number; user_id?: string; api_key_id?: string; outcome?: string
  bucket?: string
  attempts: number; billable_attempts: number; failed: number
  execution_ms: number; avg_execution_ms: number | null; cost_ucents?: number
}
export interface TopAnswer { by: string; start: number; end: number; sort: string; rows: TopRow[]; money?: boolean }

/** /stats/heatmap: attempts[weekday][hour], weekday 0 = Monday. */
export interface HeatmapAnswer { start: number; end: number; time_zone: string; weekdays: string[]; attempts: number[][] }
