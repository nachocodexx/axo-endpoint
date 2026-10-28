import { CHART_COLORS } from 'axo-ui-kit/app/utils/status'

// How a job attempt ended (the Control Plane's `outcome`), in charts.
export const OUTCOMES: Record<string, { label: string; color: string }> = {
  completed: { label: 'Completed', color: CHART_COLORS.green },
  failed_user: { label: 'Function error', color: CHART_COLORS.red },
  failed_platform: { label: 'Platform error', color: CHART_COLORS.orange },
  timed_out_in_queue: { label: 'Waited too long', color: CHART_COLORS.violet },
  cancelled: { label: 'Cancelled', color: CHART_COLORS.grey },
}
export const OUTCOME_ORDER = Object.keys(OUTCOMES)
export const outcomeName = (g: string | null) => OUTCOMES[g ?? '']?.label ?? g ?? 'Other'
export const outcomeColor = (g: string | null) => OUTCOMES[g ?? '']?.color
