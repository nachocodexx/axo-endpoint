// A live feed of the page's VE: its VEM's WebSocket topic relayed by the
// Control Plane (/ves/{id}/ws/<topic>), e.g. 'endpoints' or
// 'endpoints/<endpoint id>'. Open while `topic` gives a value (null = closed,
// e.g. while the VE isn't running), reconnecting with backoff, closed when
// the component unmounts. Browser only.
export function useVeLive(topic: MaybeRefOrGetter<string | null>, onMessage: (data: any) => void) {
  const { id } = useCurrentVe()
  const { wsUrl } = useApi()
  const connected = ref(false)
  let socket: WebSocket | null = null
  let retryMs = 1000
  let timer: ReturnType<typeof setTimeout> | null = null
  let wanted: string | null = null

  function close() {
    if (timer) clearTimeout(timer)
    timer = null
    const s = socket
    socket = null
    s?.close()
    connected.value = false
  }

  function open(path: string) {
    const ws = new WebSocket(wsUrl(path))
    socket = ws
    ws.onopen = () => { connected.value = true; retryMs = 1000 }
    ws.onmessage = (msg) => {
      try { onMessage(JSON.parse(msg.data)) } catch { /* not JSON: ignored */ }
    }
    ws.onclose = (ev) => {
      if (socket !== ws) return            // replaced or closed on purpose
      socket = null
      connected.value = false
      if (ev.code === 4401 || ev.code === 4403 || wanted !== path) return
      timer = setTimeout(() => { if (wanted === path) open(path) }, retryMs)
      retryMs = Math.min(retryMs * 2, 30_000)
    }
  }

  onMounted(() => {
    watch(() => {
      const t = toValue(topic)
      return t && id.value ? `/ves/${id.value}/ws/${t}` : null
    }, (path) => {
      wanted = path
      close()
      retryMs = 1000
      if (path) open(path)
    }, { immediate: true })
  })
  onBeforeUnmount(() => { wanted = null; close() })

  return { connected }
}
