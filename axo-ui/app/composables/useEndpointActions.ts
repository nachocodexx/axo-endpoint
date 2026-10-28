// What an Admin can do to one endpoint, with the same confirmations on the
// list and on the endpoint's page. Each returns true when it went through.
export function useEndpointActions() {
  const { api } = useCurrentVe()
  const confirmDialog = useConfirm()
  const notify = useNotifier()
  const busy = ref<string | null>(null)

  async function run(key: string, call: () => Promise<unknown>, success: string, failure: string) {
    busy.value = key
    try {
      await call()
      notify.success(success)
      return true
    } catch (err) {
      notify.error(err, failure)
      return false
    } finally {
      busy.value = null
    }
  }

  async function restart(e: EndpointRow) {
    if (!await confirmDialog.confirm({
      title: `Restart ${e.endpoint_id}?`, confirmText: 'Restart',
      text: 'Its container restarts; jobs running on it fail and new ones go to the other endpoints meanwhile.',
      consequences: ['Its runners start cold again (the first job of each function waits for that).'],
    })) return false
    return run(`restart:${e.endpoint_id}`, () => api(`/endpoints/${e.endpoint_id}/restart`, { method: 'POST' }),
      `Restarting ${e.endpoint_id}`, `${e.endpoint_id} couldn't restart`)
  }

  async function remove(e: EndpointRow, target: number) {
    if (!await confirmDialog.confirm({
      title: `Remove ${e.endpoint_id}?`, danger: true, confirmText: 'Remove',
      text: 'It stops taking new jobs, finishes the ones it is running, then goes away.',
      consequences: [`The VE's target goes down to ${Math.max(0, target - 1)} endpoint${target - 1 === 1 ? '' : 's'}.`,
        'Functions, objects and data stay: they live in the VE, not in the endpoint.'],
    })) return false
    return run(`remove:${e.endpoint_id}`, () => api(`/endpoints/${e.endpoint_id}`, { method: 'DELETE' }),
      `Removing ${e.endpoint_id}`, `${e.endpoint_id} couldn't be removed`)
  }

  async function purge(e: EndpointRow) {
    if (!await confirmDialog.confirm({
      title: `Forget ${e.endpoint_id}?`, danger: true, confirmText: 'Forget',
      text: 'It is gone already; this takes it off the endpoints list for good.',
      consequences: ['Its history (when it started, who removed it) stays in Activity until the VE\'s history period ends.',
        'Job records and statistics (and what they cost) stay.'],
    })) return false
    return run(`purge:${e.endpoint_id}`, () => api(`/endpoints/${e.endpoint_id}/purge`, { method: 'DELETE' }),
      `${e.endpoint_id} forgotten`, `${e.endpoint_id} couldn't be forgotten`)
  }

  return { busy, restart, remove, purge }
}
