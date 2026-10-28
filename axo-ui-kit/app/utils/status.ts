// One look per status, used by AxoStatusChip and the charts.

// `ring`: shown as the running ring (AxoRunningRing) instead of the icon.
export interface StatusLook { color: string; icon: string; moving?: boolean; ring?: boolean; label?: string }

const LIFECYCLE: Record<string, StatusLook> = {
  Running: { color: 'success', icon: 'mdi-check-circle' },
  Creating: { color: 'info', icon: 'mdi-progress-clock', moving: true },
  Starting: { color: 'info', icon: 'mdi-play-circle-outline', moving: true },
  Restoring: { color: 'info', icon: 'mdi-backup-restore', moving: true },
  Waking: { color: 'info', icon: 'mdi-weather-sunset-up', moving: true },
  Scaling: { color: 'info', icon: 'mdi-arrow-expand-vertical', moving: true },
  Stopping: { color: 'warning', icon: 'mdi-stop-circle-outline', moving: true },
  Stopped: { color: 'muted', icon: 'mdi-stop-circle' },
  Sleeping: { color: 'brand-3', icon: 'mdi-sleep' },
  Deleting: { color: 'error', icon: 'mdi-delete-clock', moving: true },
  Deleted: { color: 'muted', icon: 'mdi-delete' },
  Failed: { color: 'error', icon: 'mdi-alert-circle' },
}

const HEALTH: Record<string, StatusLook> = {
  Healthy: { color: 'success', icon: 'mdi-heart-pulse' },
  Degraded: { color: 'warning', icon: 'mdi-alert' },
  Unavailable: { color: 'error', icon: 'mdi-close-octagon' },
}

const ENDPOINT: Record<string, StatusLook> = {
  Running: { color: 'success', icon: 'mdi-check-circle' },
  Creating: { color: 'info', icon: 'mdi-progress-clock', moving: true },
  Starting: { color: 'info', icon: 'mdi-play-circle-outline', moving: true },
  Draining: { color: 'warning', icon: 'mdi-water-outline', moving: true },
  Removing: { color: 'warning', icon: 'mdi-minus-circle-outline', moving: true },
  Removed: { color: 'muted', icon: 'mdi-minus-circle' },
  Stopped: { color: 'muted', icon: 'mdi-stop-circle' },
  Sleeping: { color: 'brand-3', icon: 'mdi-sleep' },
  Failed: { color: 'error', icon: 'mdi-alert-circle' },
  Unreachable: { color: 'error', icon: 'mdi-lan-disconnect' },
}

const JOB: Record<string, StatusLook> = {
  completed: { color: 'success', icon: 'mdi-check-circle', label: 'Completed' },
  COMPLETED: { color: 'success', icon: 'mdi-check-circle', label: 'Completed' },
  failed_user: { color: 'error', icon: 'mdi-bug', label: 'Function error' },
  failed_platform: { color: 'warning', icon: 'mdi-server-off', label: 'Platform error' },
  FAILED: { color: 'error', icon: 'mdi-alert-circle', label: 'Failed' },
  cancelled: { color: 'muted', icon: 'mdi-cancel', label: 'Cancelled' },
  CANCELLED: { color: 'muted', icon: 'mdi-cancel', label: 'Cancelled' },
  timed_out_in_queue: { color: 'warning', icon: 'mdi-timer-sand-complete', label: 'Waited too long' },
  PENDING: { color: 'info', icon: 'mdi-timer-sand', moving: true, label: 'Pending' },
  RUNNING: { color: 'info', icon: 'mdi-play-circle-outline', ring: true, label: 'Running' },
  QUEUED: { color: 'info', icon: 'mdi-tray-full', moving: true, label: 'Queued' },
  WAITING_RETRY: { color: 'warning', icon: 'mdi-timer-refresh-outline', moving: true, label: 'Waiting to retry' },
}

// A function (its newest version): ready to run, its runner starting / gone.
const FUNCTION: Record<string, StatusLook> = {
  ready: { color: 'success', icon: 'mdi-check-circle', label: 'Ready' },
  starting: { color: 'info', icon: 'mdi-play-circle-outline', moving: true, label: 'Starting' },
  stopped: { color: 'muted', icon: 'mdi-stop-circle', label: 'Stopped' },
  crashed: { color: 'error', icon: 'mdi-alert-circle', label: 'Crashed' },
  deleted: { color: 'muted', icon: 'mdi-delete', label: 'Deleted' },
}

// An active object: its __init__ still running, or ready for calls.
const OBJECT: Record<string, StatusLook> = {
  creating: { color: 'info', icon: 'mdi-progress-clock', moving: true, label: 'Creating' },
  ready: { color: 'success', icon: 'mdi-check-circle', label: 'Ready' },
}

const BILL: Record<string, StatusLook> = {
  open: { color: 'info', icon: 'mdi-receipt-text-clock-outline', moving: true, label: 'Open' },
  closed: { color: 'success', icon: 'mdi-receipt-text-check-outline', label: 'Closed' },
}

// A choreography run, or one of its steps.
const RUN: Record<string, StatusLook> = {
  queued: { color: 'info', icon: 'mdi-tray-full', moving: true, label: 'Queued' },
  pending: { color: 'muted', icon: 'mdi-circle-outline', label: 'Waiting' },
  running: { color: 'info', icon: 'mdi-play-circle-outline', ring: true, label: 'Running' },
  completed: { color: 'success', icon: 'mdi-check-circle', label: 'Completed' },
  completed_with_errors: { color: 'warning', icon: 'mdi-alert-circle-check', label: 'Completed with errors' },
  failed: { color: 'error', icon: 'mdi-close-circle', label: 'Failed' },
  skipped: { color: 'muted', icon: 'mdi-debug-step-over', label: 'Skipped' },
  cancelled: { color: 'muted', icon: 'mdi-cancel', label: 'Cancelled' },
  not_taken: { color: 'muted', icon: 'mdi-call-split', label: 'Not taken' },
}

const TABLES = { lifecycle: LIFECYCLE, health: HEALTH, endpoint: ENDPOINT, job: JOB, bill: BILL, function: FUNCTION, object: OBJECT, run: RUN }
export type StatusKind = keyof typeof TABLES

export function statusLook(kind: StatusKind, value: string | null | undefined): StatusLook {
  if (!value) return { color: 'muted', icon: 'mdi-minus-circle-outline', label: '—' }
  return TABLES[kind][value] ?? { color: 'muted', icon: 'mdi-help-circle-outline' }
}

/** Hex colors for charts (ECharts can't read Vuetify theme names). */
export const CHART_COLORS = {
  blue: '#0086FF', cyan: '#00C2FF', violet: '#5B5BFF', green: '#12B886',
  orange: '#F59F00', red: '#E5484D', grey: '#8A8F98', pink: '#E64980', teal: '#0CA678',
}
export const SERIES_PALETTE = ['#0086FF', '#00C2FF', '#5B5BFF', '#12B886', '#F59F00', '#E64980', '#0CA678', '#845EF7']
