import type { Notice, VeCard } from '../types/cp'

type VeListener = (ve: VeCard | null, veId: string) => void

const veListeners = new Set<VeListener>()
let socket: WebSocket | null = null
let retryMs = 1000
let stopped = true
let connectedAt = 0

// The Control Plane's live channel (GET /ws): new notices go to the bell
// and, when they happen while the person is here, to a snackbar; VE card
// changes go to whoever listens (lists, the dashboard). Notices that were
// already there when the socket opened only go to the bell (agreed).
export function useLive() {
  const connected = useState('axo-live-connected', () => false)

  function start() {
    if (!import.meta.client || socket) return
    stopped = false
    open()
  }

  function open() {
    const { wsUrl, session } = useApi()
    if (!session.value) return
    const notices = useNotificationsStore()
    const notify = useNotifier()
    connectedAt = Date.now() / 1000
    const ws = new WebSocket(wsUrl('/ws'))
    socket = ws
    ws.onopen = () => { connected.value = true; retryMs = 1000 }
    ws.onmessage = (msg) => {
      let data: any
      try { data = JSON.parse(msg.data) } catch { return }
      if (data.type === 'notification') {
        const { type: _t, ...note } = data
        const fresh = notices.add(note as Notice)
        if (fresh && (note as Notice).created_at >= connectedAt - 5) {
          const invitationId = note.kind === 'invitation_received' ? note.data?.invitation_id : null
          notify.push({
            kind: notificationKind(note), text: noticeText(note), to: notificationLink(note),
            action: invitationId ? { text: 'Accept', run: () => { useInvitations().answer(invitationId, true).catch(() => {}) } } : undefined,
          })
        }
      } else if (data.type === 've') {
        veListeners.forEach(l => l(data.ve as VeCard, data.ve.ve_id))
      } else if (data.type === 've_removed') {
        veListeners.forEach(l => l(null, data.ve_id))
      }
    }
    ws.onclose = (ev) => {
      connected.value = false
      socket = null
      if (stopped || ev.code === 4401) return
      setTimeout(() => { if (!stopped) open() }, retryMs)
      retryMs = Math.min(retryMs * 2, 30_000)
    }
  }

  function stop() {
    stopped = true
    socket?.close()
    socket = null
    connected.value = false
  }

  /** Listen to VE card changes for as long as the calling component lives. */
  function onVe(listener: VeListener) {
    veListeners.add(listener)
    if (getCurrentInstance()) onBeforeUnmount(() => veListeners.delete(listener))
    return () => veListeners.delete(listener)
  }

  return { connected, start, stop, onVe }
}
