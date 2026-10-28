import type { Invitation, Transfer } from '../utils/teams'

// The inbox: invitations and transfers waiting for this person (or for a
// team they answer for), the ones they sent, and the last 30 days of
// answers. Shared by the inbox page, the Teams list (team invitations on
// top) and the account menu's count; reloads after any answer and when a
// notice about them arrives.
export function useInbox() {
  const received = useState<Invitation[]>('inbox-received', () => [])
  const sent = useState<Invitation[]>('inbox-sent', () => [])
  const answered = useState<Invitation[]>('inbox-answered', () => [])
  const transfers = useState<Transfer[]>('inbox-transfers', () => [])
  const loaded = useState('inbox-loaded', () => false)
  const error = useState<unknown>('inbox-error', () => null)
  const me = useMeStore()

  async function load() {
    const { apiFetch } = useApi()
    error.value = null
    try {
      const [r, s, a, t] = await Promise.all([
        apiFetch<Invitation[]>('/invitations'), apiFetch<Invitation[]>('/invitations/sent'),
        apiFetch<Invitation[]>('/invitations/answered'), apiFetch<Transfer[]>('/transfers', { query: { answered_days: 30 } }),
      ])
      received.value = r; sent.value = s; answered.value = a; transfers.value = t
      loaded.value = true
    } catch (err) { error.value = err }
  }

  const myTeamIds = computed(() => new Set((me.me?.teams ?? []).map(t => t.team_id)))
  /** Transfers someone else asked for that this person (or a team they own) answers. */
  const transfersIn = computed(() => transfers.value.filter(t => t.status === 'pending' && t.from !== me.me?.user_id))
  const transfersOut = computed(() => transfers.value.filter(t => t.status === 'pending' && t.from === me.me?.user_id))
  const transfersDone = computed(() => transfers.value.filter(t => t.status !== 'pending'))
  /** What waits for an answer: the account menu's count. */
  const waiting = computed(() => received.value.length + transfersIn.value.length)
  const teamInvitations = computed(() => received.value.filter(i => i.kind === 'team' && i.invitee.type === 'user'))

  return { received, sent, answered, transfers, transfersIn, transfersOut, transfersDone, waiting, teamInvitations,
           myTeamIds, loaded, error, load }
}

/** Keep the inbox current for as long as the calling component lives. */
export function useInboxFollow() {
  const inbox = useInbox()
  const invitations = useInvitations()
  const notices = useNotificationsStore()
  const KINDS = new Set(['invitation_received', 'invitation_answered', 'transfer_requested', 'transfer_completed'])
  onMounted(() => { if (!inbox.loaded.value) inbox.load() })
  watch(invitations.version, () => inbox.load())
  watch(() => notices.items[0], (n) => { if (n && KINDS.has(n.kind)) inbox.load() })
  return inbox
}
