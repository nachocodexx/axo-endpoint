// Answering an invitation from anywhere (the bell, a snackbar, the inbox).
// `answers` remembers what was answered in this tab so every place shows
// it at once; `version` goes up after each answer, for pages that list
// invitations (or what accepting gave: teams, VEs, buckets) to reload.
export type InvitationAnswer = 'accepted' | 'rejected' | 'gone'

export function useInvitations() {
  const answers = useState<Record<string, InvitationAnswer>>('axo-invitation-answers', () => ({}))
  const version = useState('axo-invitations-version', () => 0)

  async function answer(invitationId: string, accept: boolean): Promise<InvitationAnswer> {
    const { apiFetch } = useApi()
    const notify = useNotifier()
    try {
      const done = await apiFetch<{ target_name: string; kind: string; invitee: { type: string; name: string } }>(
        `/invitations/${invitationId}/${accept ? 'accept' : 'reject'}`, { method: 'POST' })
      answers.value = { ...answers.value, [invitationId]: accept ? 'accepted' : 'rejected' }
      if (accept) {
        const what = done.kind === 'team' ? `the team ${done.target_name}` : `${done.kind === 've' ? 'the VE' : 'the bucket'} ${done.target_name}`
        notify.success(done.invitee.type === 'team' ? `${done.invitee.name} now has access to ${what}`
          : done.kind === 'team' ? `You joined ${what}` : `You now have access to ${what}`)
        await useMeStore().load(true)
      } else {
        notify.info(`Invitation to ${done.target_name} declined`)
      }
    } catch (err) {
      if (err instanceof AxoApiError && err.status === 404) {
        answers.value = { ...answers.value, [invitationId]: 'gone' }
        notify.info('This invitation was already answered or withdrawn')
      } else {
        notify.error(err, "The invitation couldn't be answered")
        throw err
      }
    } finally {
      version.value++
    }
    return answers.value[invitationId]!
  }

  return { answers, version, answer }
}
