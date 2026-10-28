import type { Job } from '../utils/jobs'

/** What a person can do to a job (Developer and above): cancel (asks first
 *  only when it's running: that time is billed), retry (a new attempt of
 *  the same job), run again (a new job, the run form filled in) and
 *  download its result. Each resolves true when it worked. */
export function useJobActions() {
  const { id, api, can } = useCurrentVe()
  const confirmDialog = useConfirm()
  const notifier = useNotifier()
  const busy = ref<string | null>(null)

  const canCancel = (j: Job) => can('developer') && isActive(j)
  const canRetry = (j: Job) => can('developer') && (j.status === 'FAILED' || j.status === 'CANCELLED')
    && j.kind === 'function' && !j.choreography
  const hasResult = (j: Job) => j.status === 'COMPLETED' && !!j.result?.stored && !j.result?.deleted_at

  async function cancel(j: Job): Promise<boolean> {
    if (j.status === 'RUNNING') {
      const ran = j.started_at ? formatDuration(Date.now() - Date.parse(j.started_at)) : 'a moment'
      if (!await confirmDialog.confirm({
        title: 'Cancel this running job?', danger: true, confirmText: 'Cancel the job', cancelText: 'Let it run',
        text: `It has been running for ${ran}; it stops now.`,
        consequences: ['The time it ran is billed.', 'Its result is not kept; it is not tried again.'],
      })) return false
    }
    busy.value = `cancel:${j.job_id}`
    try {
      await api(`/jobs/${j.job_id}/cancel`, { method: 'POST' })
      notifier.success('Job cancelled', { to: `/ves/${id.value}/jobs/${j.job_id}` })
      return true
    } catch (err) {
      notifier.error(err, "The job couldn't be cancelled")
      return false
    } finally { busy.value = null }
  }

  async function retry(j: Job): Promise<boolean> {
    busy.value = `retry:${j.job_id}`
    try {
      const answer = await api<{ attempt: number }>(`/jobs/${j.job_id}/retry`, { method: 'POST' })
      notifier.success(`Trying again: attempt ${answer.attempt}`, { to: `/ves/${id.value}/jobs/${j.job_id}` })
      return true
    } catch (err) {
      notifier.error(err, "The job couldn't be tried again")
      return false
    } finally { busy.value = null }
  }

  function runAgain(j: Job) {
    return navigateTo({ path: `/ves/${id.value}/functions/${j.function_id}/run`,
      query: { version: j.function_version ?? undefined, from: j.job_id } })
  }

  async function downloadResult(j: Job): Promise<boolean> {
    busy.value = `download:${j.job_id}`
    try {
      const value = await api(`/jobs/${j.job_id}/result`)
      saveBlob(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }), `${j.job_id}.json`)
      return true
    } catch (err) {
      notifier.error(err, "The result couldn't be downloaded")
      return false
    } finally { busy.value = null }
  }

  return { busy, canCancel, canRetry, hasResult, cancel, retry, runAgain, downloadResult }
}
