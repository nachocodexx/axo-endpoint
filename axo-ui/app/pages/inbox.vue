<template>
  <div>
    <AxoPageHeader title="Inbox" icon="mdi-inbox-arrow-down-outline"
                   subtitle="Invitations and transfers waiting for an answer."
                   help="An invitation gives you (or a team you manage) a role in a team, a VE or a bucket. A transfer makes you the owner of something -- and so the one who pays for it. Nothing changes until it is accepted.">
      <template #actions>
        <v-btn variant="text" icon="mdi-refresh" aria-label="Refresh" :loading="refreshing" @click="refresh" />
      </template>
    </AxoPageHeader>

    <AxoErrorAlert v-if="inbox.error.value" :error="inbox.error.value" title="Your inbox couldn't load" :retry="inbox.load" class="mb-4" />

    <v-tabs v-model="tab" color="primary" class="mb-4">
      <v-tab value="invitations" prepend-icon="mdi-email-plus-outline">
        Invitations<v-badge v-if="inbox.received.value.length" :content="inbox.received.value.length" color="error" inline />
      </v-tab>
      <v-tab value="transfers" prepend-icon="mdi-swap-horizontal">
        Transfers<v-badge v-if="inbox.transfersIn.value.length" :content="inbox.transfersIn.value.length" color="error" inline />
      </v-tab>
    </v-tabs>

    <AxoLoader v-if="!inbox.loaded.value && !inbox.error.value" block />

    <template v-else-if="tab === 'invitations'">
      <AxoPanel title="Waiting for you" icon="mdi-email-plus-outline" stacked flush class="mb-6"
                help="Invitations to you, and to teams where you are Owner or Admin (any of them can answer for the team).">
        <AxoEmptyState v-if="!inbox.received.value.length" icon="mdi-email-check-outline" title="Nothing waiting"
                       text="When someone invites you to a team, a VE or a bucket, it shows up here and in the bell." />
        <div v-for="inv in inbox.received.value" :key="inv.id" class="axo-row">
          <v-icon :color="KIND_COLORS[inv.kind]" class="mt-1">{{ KIND_ICONS[inv.kind] }}</v-icon>
          <div class="flex-grow-1" style="min-width: 0">
            <div class="text-body-1">
              <b>{{ inv.invited_by_name }}</b> invited
              {{ inv.invitee.type === 'team' ? inv.invitee.name : 'you' }} to {{ targetTitle(inv.kind, '') }}<b>{{ inv.target_name }}</b>
              as {{ invitationRoleTitle(inv) }}
              <v-chip v-if="inv.invitee.type === 'team'" size="x-small" variant="tonal" class="ml-1">for {{ inv.invitee.name }}</v-chip>
            </div>
            <div class="text-body-2 axo-muted">{{ acceptMeans(inv) }}</div>
            <div class="text-caption axo-muted mt-1">{{ timeAgo(inv.created_at) }}</div>
          </div>
          <div class="d-flex ga-2 align-center flex-shrink-0">
            <AxoBtn size="small" :loading="busy === inv.id" @click="answer(inv, true)">Accept</AxoBtn>
            <v-btn size="small" variant="text" class="text-none" :disabled="busy === inv.id" @click="answer(inv, false)">Decline</v-btn>
          </div>
        </div>
      </AxoPanel>

      <AxoPanel title="Sent by you" icon="mdi-email-fast-outline" stacked flush class="mb-6"
                help="Invitations you sent that nobody answered yet. Cancelling one means it can't be accepted any more.">
        <AxoEmptyState v-if="!inbox.sent.value.length" icon="mdi-email-outline" title="Nothing pending"
                       text="Invite people from a team's Members tab, or from a VE's or bucket's Access tab." />
        <div v-for="inv in inbox.sent.value" :key="inv.id" class="axo-row">
          <v-icon :color="KIND_COLORS[inv.kind]" class="mt-1">{{ KIND_ICONS[inv.kind] }}</v-icon>
          <div class="flex-grow-1" style="min-width: 0">
            <div class="text-body-1">You invited <b>{{ inv.invitee.name }}</b>
              <span v-if="inv.invitee.type === 'email'" class="axo-muted"> (by email)</span>
              to {{ targetTitle(inv.kind, '') }}<b>{{ inv.target_name }}</b> as {{ invitationRoleTitle(inv) }}</div>
            <div class="text-caption axo-muted mt-1">waiting · {{ timeAgo(inv.created_at) }}</div>
          </div>
          <v-btn size="small" variant="text" class="text-none" :loading="busy === inv.id" @click="cancelInvitation(inv)">Cancel</v-btn>
        </div>
      </AxoPanel>

      <AxoPanel v-if="inbox.answered.value.length" title="Answered (last 30 days)" icon="mdi-history" stacked flush>
        <div v-for="inv in inbox.answered.value" :key="inv.id" class="axo-row axo-row--done">
          <v-icon :color="STATUS[inv.status].color" size="20" class="mt-1">{{ STATUS[inv.status].icon }}</v-icon>
          <div class="flex-grow-1 text-body-2" style="min-width: 0">
            {{ inv.invited_by === me.me?.user_id ? `${inv.invitee.name}` : inv.invitee.type === 'team' ? inv.invitee.name : 'You' }}
            {{ STATUS[inv.status].verb }} the invitation to {{ targetTitle(inv.kind, inv.target_name) }}
            <span v-if="inv.invited_by !== me.me?.user_id" class="axo-muted">from {{ inv.invited_by_name }}</span>
          </div>
          <span class="text-caption axo-muted flex-shrink-0">{{ timeAgo(inv.answered_at ?? inv.created_at) }}</span>
        </div>
      </AxoPanel>
    </template>

    <template v-else>
      <AxoPanel title="Waiting for you" icon="mdi-swap-horizontal" stacked flush class="mb-6"
                help="Someone wants to make you (or a team you own) the owner of something. The owner pays for it, so read what it means before accepting.">
        <AxoEmptyState v-if="!inbox.transfersIn.value.length" icon="mdi-swap-horizontal" title="Nothing waiting"
                       text="When someone wants to give you a VE, a bucket or a team, it shows up here." />
        <div v-for="t in inbox.transfersIn.value" :key="t.id" class="axo-row">
          <v-icon :color="KIND_COLORS[t.kind]" class="mt-1">{{ KIND_ICONS[t.kind] }}</v-icon>
          <div class="flex-grow-1" style="min-width: 0">
            <div class="text-body-1"><b>{{ t.from_name }}</b> wants to give {{ t.to.type === 'team' ? t.to.name : 'you' }}
              {{ targetTitle(t.kind, '') }}<b>{{ t.target_name }}</b></div>
            <div class="text-body-2 mt-1" :class="t.kind === 've' && t.plan?.monthly_fee_cents ? 'text-warning' : 'axo-muted'">
              <v-icon v-if="t.kind === 've' && t.plan?.monthly_fee_cents" size="16" class="mr-1">mdi-cash</v-icon>{{ transferMeans(t) }}
            </div>
            <div class="text-caption axo-muted mt-1">{{ timeAgo(t.created_at) }}</div>
          </div>
          <div class="d-flex ga-2 align-center flex-shrink-0">
            <AxoBtn size="small" :loading="busy === t.id" @click="answerTransfer(t, true)">Accept…</AxoBtn>
            <v-btn size="small" variant="text" class="text-none" :disabled="busy === t.id" @click="answerTransfer(t, false)">Decline</v-btn>
          </div>
        </div>
      </AxoPanel>

      <AxoPanel title="Asked by you" icon="mdi-send-outline" stacked flush class="mb-6"
                help="Transfers you asked for. Until they're accepted you are still the owner, and you can cancel them.">
        <AxoEmptyState v-if="!inbox.transfersOut.value.length" icon="mdi-send-outline" title="Nothing pending"
                       text="Start a transfer from a VE's, bucket's or team's Settings." />
        <div v-for="t in inbox.transfersOut.value" :key="t.id" class="axo-row">
          <v-icon :color="KIND_COLORS[t.kind]" class="mt-1">{{ KIND_ICONS[t.kind] }}</v-icon>
          <div class="flex-grow-1" style="min-width: 0">
            <div class="text-body-1">You want to give <b>{{ t.to.name }}</b> {{ targetTitle(t.kind, '') }}<b>{{ t.target_name }}</b></div>
            <div class="text-caption axo-muted mt-1">waiting · {{ timeAgo(t.created_at) }}</div>
          </div>
          <v-btn size="small" variant="text" class="text-none" :loading="busy === t.id" @click="cancelTransfer(t)">Cancel</v-btn>
        </div>
      </AxoPanel>

      <AxoPanel v-if="inbox.transfersDone.value.length" title="Answered (last 30 days)" icon="mdi-history" stacked flush>
        <div v-for="t in inbox.transfersDone.value" :key="t.id" class="axo-row axo-row--done">
          <v-icon :color="STATUS[t.status].color" size="20" class="mt-1">{{ STATUS[t.status].icon }}</v-icon>
          <div class="flex-grow-1 text-body-2" style="min-width: 0">
            {{ t.kind === 've' ? 'VE' : t.kind === 'bucket' ? 'Bucket' : 'Team' }} <b>{{ t.target_name }}</b>
            from {{ t.from_name }} to {{ t.to.name }}: {{ STATUS[t.status].title.toLowerCase() }}
          </div>
          <span class="text-caption axo-muted flex-shrink-0">{{ timeAgo(t.answered_at ?? t.created_at) }}</span>
        </div>
      </AxoPanel>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { Invitation, Transfer } from '../utils/teams'

// Everything that needs someone's answer: invitations (also answerable from
// the bell) and transfers (here only: the new owner pays, so it's confirmed).
useHead({ title: 'Inbox · axo' })
const route = useRoute()
const me = useMeStore()
const notify = useNotifier()
const confirmDialog = useConfirm()
const invitations = useInvitations()
const inbox = useInboxFollow()
const { apiFetch } = useApi()

const tab = ref(route.query.tab === 'transfers' ? 'transfers' : 'invitations')
watch(tab, t => navigateTo({ query: t === 'invitations' ? {} : { tab: t } }, { replace: true }))
watch(() => route.query.tab, t => { tab.value = t === 'transfers' ? 'transfers' : 'invitations' })

const KIND_ICONS = { team: 'mdi-account-group-outline', ve: 'mdi-cube-outline', bucket: 'mdi-database-outline' }
const KIND_COLORS = { team: 'primary', ve: 'primary', bucket: 'primary' }
const STATUS = {
  pending: { title: 'Waiting', verb: 'has not answered', icon: 'mdi-clock-outline', color: 'muted' },
  accepted: { title: 'Accepted', verb: 'accepted', icon: 'mdi-check-circle-outline', color: 'success' },
  rejected: { title: 'Declined', verb: 'declined', icon: 'mdi-close-circle-outline', color: 'muted' },
  cancelled: { title: 'Cancelled', verb: 'cancelled', icon: 'mdi-cancel', color: 'muted' },
}

const refreshing = ref(false)
async function refresh() { refreshing.value = true; try { await inbox.load() } finally { refreshing.value = false } }

const busy = ref<string | null>(null)
async function answer(inv: Invitation, accept: boolean) {
  busy.value = inv.id
  try { await invitations.answer(inv.id, accept) } catch { /* said by the notifier */ } finally { busy.value = null }
}

async function cancelInvitation(inv: Invitation) {
  busy.value = inv.id
  try {
    await apiFetch(`/invitations/${inv.id}`, { method: 'DELETE' })
    inbox.sent.value = inbox.sent.value.filter(i => i.id !== inv.id)
    notify.success(`Invitation to ${inv.invitee.name} cancelled`)
  } catch (err) { notify.error(err, "The invitation couldn't be cancelled") } finally { busy.value = null }
}

async function answerTransfer(t: Transfer, accept: boolean) {
  if (accept && !await confirmDialog.confirm({
    title: `Become the owner of ${t.target_name}?`, confirmText: 'Accept the transfer',
    text: transferMeans(t),
    consequences: t.kind === 'team' ? [] : ['The owner sees and pays its bills, and decides who has access.'],
  })) return
  busy.value = t.id
  try {
    await apiFetch(`/transfers/${t.id}/${accept ? 'accept' : 'reject'}`, { method: 'POST' })
    notify.success(accept ? `${t.target_name} is now ${t.to.type === 'team' ? t.to.name + "'s" : 'yours'}` : 'Transfer declined')
    if (accept) { await me.load(true); useVesStore().load() }
    await inbox.load()
  } catch (err) { notify.error(err, "The transfer couldn't be answered") } finally { busy.value = null }
}

async function cancelTransfer(t: Transfer) {
  busy.value = t.id
  try {
    await apiFetch(`/transfers/${t.id}`, { method: 'DELETE' })
    notify.success(`You're keeping ${t.target_name}`)
    await inbox.load()
  } catch (err) { notify.error(err, "The transfer couldn't be cancelled") } finally { busy.value = null }
}
</script>

<style scoped>
.axo-row { display: flex; gap: 14px; align-items: flex-start; padding: 14px 20px; flex-wrap: wrap; }
.axo-row + .axo-row { border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
.axo-row--done { padding: 10px 20px; align-items: center; }
@media (max-width: 600px) { .axo-row > .flex-shrink-0 { width: 100%; justify-content: flex-end; } }
</style>
