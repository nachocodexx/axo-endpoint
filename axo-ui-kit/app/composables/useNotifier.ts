import type { RouteLocationRaw } from 'vue-router'
import { AxoApiError } from '../utils/apiError'

export type SnackKind = 'success' | 'info' | 'warning' | 'error'

export interface Snack {
  id: number
  kind: SnackKind
  title?: string
  text: string
  /** Where clicking the snackbar goes (the subject: a VE, a job, a bill...). */
  to?: RouteLocationRaw
  /** For errors: catalog name + number, linked to /errors#NAME. */
  errorName?: string
  errorCode?: number
  /** A button on the snackbar (e.g. Undo); clicking it also closes it. */
  action?: { text: string; run: () => void }
  timeout: number
  createdAt: number
}

/** How long each kind stays (agreed: 5 s / 5 s / 8 s / 10 s). */
export const SNACK_TIMEOUTS: Record<SnackKind, number> = { success: 5000, info: 5000, warning: 8000, error: 10000 }
export const MAX_VISIBLE_SNACKS = 3

let nextId = 1

// Timed, stacked snackbars (AxoSnackbars shows at most 3; the rest wait).
export function useNotifier() {
  const snacks = useState<Snack[]>('axo-snacks', () => [])

  function push(s: Omit<Snack, 'id' | 'timeout' | 'createdAt'> & { timeout?: number }): number {
    const snack: Snack = { ...s, id: nextId++, timeout: s.timeout ?? SNACK_TIMEOUTS[s.kind], createdAt: Date.now() }
    snacks.value = [...snacks.value, snack]
    return snack.id
  }

  function dismiss(id: number) {
    snacks.value = snacks.value.filter(s => s.id !== id)
  }

  const success = (text: string, extra: Partial<Snack> = {}) => push({ kind: 'success', text, ...extra })
  const info = (text: string, extra: Partial<Snack> = {}) => push({ kind: 'info', text, ...extra })
  const warning = (text: string, extra: Partial<Snack> = {}) => push({ kind: 'warning', text, ...extra })

  /** Shows any failure with its catalog name + number. `title`: what failed ("Couldn't stop ml-prod"). */
  function error(err: unknown, title?: string, extra: Partial<Snack> = {}) {
    const e = AxoApiError.from(err)
    return push({ kind: 'error', title, text: e.message, errorName: e.errorName, errorCode: e.errorCode, ...extra })
  }

  return { snacks, push, dismiss, success, info, warning, error }
}
