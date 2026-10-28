// Runs `fn` now and every `everyMs` while the page is visible (the
// dashboard refreshes every 30 s). Stops when the component unmounts.
export function usePolling(fn: () => unknown | Promise<unknown>, everyMs = 30_000) {
  let timer: ReturnType<typeof setInterval> | null = null
  const lastRun = ref<number | null>(null)

  async function tick() {
    if (import.meta.client && document.visibilityState === 'hidden') return
    await fn()
    lastRun.value = Date.now()
  }

  function onVisible() {
    if (document.visibilityState === 'visible' && (!lastRun.value || Date.now() - lastRun.value > everyMs)) tick()
  }

  onMounted(() => {
    tick()
    timer = setInterval(tick, everyMs)
    document.addEventListener('visibilitychange', onVisible)
  })
  onBeforeUnmount(() => {
    if (timer) clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisible)
  })
  return { refresh: tick, lastRun }
}
