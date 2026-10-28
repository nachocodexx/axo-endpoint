import type { RouteLocationRaw } from 'vue-router'
import type { Notice } from '../types/cp'

/** Where a notice leads when clicked (its subject). */
export function notificationLink(note: Pick<Notice, 'kind' | 'data'>): RouteLocationRaw | undefined {
  const d = note.data || {}
  switch (note.kind) {
    case 'invitation_received':
    case 'invitation_answered':
      return '/inbox'
    case 'transfer_requested':
    case 'transfer_completed':
      return { path: '/inbox', query: { tab: 'transfers' } }
    case 'team_purchase':
    case 'purchase_pending':
    case 'purchase_answered':
      return d.team_id ? `/teams/${d.team_id}` : '/teams'
    case 'bill_closed':
      if (d.bill_id) return `/billing/bills/${encodeURIComponent(d.bill_id)}`
      return '/billing'
    case 'job_finished':
      if (d.ve_id && d.job_id) return `/ves/${d.ve_id}/jobs/${d.job_id}`
      break
    case 'budget_warning':
    case 'renewal_soon':
    case 'rental_ending':
    case 'plan_change_scheduled':
      if (d.ve_id) return `/ves/${d.ve_id}/costs`
      break
  }
  return d.ve_id ? `/ves/${d.ve_id}` : undefined
}

/** How loud a notice is in the snackbar. */
export function notificationKind(note: Pick<Notice, 'kind' | 'data'>): 'success' | 'info' | 'warning' | 'error' {
  const d = note.data || {}
  if (note.kind === 'budget_warning' || note.kind === 'limit_warning' || note.kind === 'rental_ending') {
    return Number(d.threshold ?? d.used ?? 0) >= 100 ? 'error' : 'warning'
  }
  if (note.kind === 've_state') return d.status === 'Failed' ? 'error' : 'info'
  if (note.kind === 'job_finished') return d.status === 'completed' || d.status === 'COMPLETED' ? 'success' : 'error'
  return 'info'
}

export const NOTIFICATION_ICONS: Record<string, string> = {
  invitation_received: 'mdi-email-plus-outline',
  invitation_answered: 'mdi-email-check-outline',
  transfer_requested: 'mdi-swap-horizontal',
  transfer_completed: 'mdi-swap-horizontal-bold',
  team_purchase: 'mdi-cart-outline',
  purchase_pending: 'mdi-cart-arrow-right',
  purchase_answered: 'mdi-cart-check',
  ve_state: 'mdi-cube-outline',
  limit_warning: 'mdi-gauge-full',
  job_finished: 'mdi-flag-checkered',
  bill_closed: 'mdi-receipt-text-check-outline',
  renewal_soon: 'mdi-autorenew',
  rental_ending: 'mdi-calendar-remove-outline',
  plan_change_scheduled: 'mdi-calendar-arrow-right',
  budget_warning: 'mdi-piggy-bank-outline',
}

/** The notifications page's type chips (the Control Plane's GROUPS). */
export const NOTIFICATION_GROUPS = [
  { value: 'people', title: 'People & teams', icon: 'mdi-account-group-outline' },
  { value: 'ves', title: 'VEs', icon: 'mdi-cube-outline' },
  { value: 'jobs', title: 'Jobs', icon: 'mdi-briefcase-outline' },
  { value: 'billing', title: 'Billing', icon: 'mdi-receipt-text-outline' },
] as const
