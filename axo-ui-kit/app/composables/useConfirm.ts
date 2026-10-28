export interface ConfirmRequest {
  title: string
  text?: string
  /** Bullet points: what will happen (e.g. "3 endpoints will be removed"). */
  consequences?: string[]
  confirmText?: string
  cancelText?: string
  /** Red button and warning icon (deletes, stops, cancels). */
  danger?: boolean
  /** The person must type this exactly (e.g. the VE's name) to confirm. */
  typeToConfirm?: string
}

interface ConfirmState extends ConfirmRequest { open: boolean; resolve?: (ok: boolean) => void }

// One dialog (AxoConfirmDialog, mounted once by AxoShell) for every
// destructive action: `if (await confirm({...})) doIt()`.
export function useConfirm() {
  const state = useState<ConfirmState>('axo-confirm', () => ({ open: false, title: '' }))

  function confirm(req: ConfirmRequest): Promise<boolean> {
    state.value.resolve?.(false)
    return new Promise((resolve) => { state.value = { ...req, open: true, resolve } })
  }

  function answer(ok: boolean) {
    const resolve = state.value.resolve
    state.value = { ...state.value, open: false, resolve: undefined }
    resolve?.(ok)
  }

  return { state, confirm, answer }
}
