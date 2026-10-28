// Optimistic UI: change the screen first, ask the server, undo on failure.
//
//   await optimistic({
//     apply: () => { ve.lifecycle = 'Stopping' },
//     rollback: () => { ve.lifecycle = before },
//     run: () => apiFetch(`/ves/${id}/stop`, { method: 'POST' }),
//     success: 'Stopping ml-prod', failure: "Couldn't stop ml-prod",
//   })
export interface OptimisticStep<T> {
  apply?: () => void
  rollback?: () => void
  run: () => Promise<T>
  /** Snackbar text on success (none when omitted). */
  success?: string | ((result: T) => string)
  /** Snackbar title on failure (the error's own message is shown under it). */
  failure?: string
  /** Called with the server's answer (e.g. to replace the guessed row with the real one). */
  settle?: (result: T) => void
}

export function useOptimistic() {
  const notify = useNotifier()
  const pending = ref(0)

  async function optimistic<T>(step: OptimisticStep<T>): Promise<T | undefined> {
    step.apply?.()
    pending.value++
    try {
      const result = await step.run()
      step.settle?.(result)
      if (step.success) notify.success(typeof step.success === 'function' ? step.success(result) : step.success)
      return result
    } catch (err) {
      step.rollback?.()
      notify.error(err, step.failure)
      return undefined
    } finally {
      pending.value--
    }
  }

  return { optimistic, pending: computed(() => pending.value > 0) }
}
