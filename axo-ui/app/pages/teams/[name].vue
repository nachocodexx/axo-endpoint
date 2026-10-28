<template>
  <div>
    <AxoLoader v-if="loading" block />
    <AxoErrorAlert v-else-if="error" :error="error" title="This team couldn't load" :retry="load" />
    <template v-else-if="team">
      <AxoPageHeader :title="team.name" icon="mdi-account-group-outline" :subtitle="team.description || undefined"
                     :crumbs="[{ title: 'Teams', to: '/teams' }, { title: team.name, disabled: true }]">
        <template #title-append>
          <v-chip size="small" variant="tonal" :color="TEAM_ROLE_INFO[myRole]?.color" class="ml-2">You: {{ teamRoleTitle(myRole) }}</v-chip>
        </template>
        <template #actions>
          <AxoBtn v-if="isAdmin" prepend-icon="mdi-account-plus-outline" @click="inviteOpen = true">Invite</AxoBtn>
        </template>
      </AxoPageHeader>

      <div class="d-flex flex-wrap ga-6 mb-4 text-body-2">
        <span><v-icon size="18" class="mr-1">mdi-account-multiple-outline</v-icon>{{ plural(team.members.length, 'member') }}</span>
        <span><v-icon size="18" class="mr-1">mdi-cube-outline</v-icon>{{ plural(owned('ve').length, 'VE') }} owned</span>
        <span><v-icon size="18" class="mr-1">mdi-database-outline</v-icon>{{ plural(owned('bucket').length, 'bucket') }} owned</span>
        <span class="axo-muted">Created {{ formatDate(team.created_at) }}</span>
      </div>

      <v-tabs v-model="tab" color="primary" class="mb-4" show-arrows>
        <v-tab value="members">Members</v-tab>
        <v-tab value="resources">VEs and buckets</v-tab>
        <v-tab v-if="isAdmin" value="spending">Spending
          <v-badge v-if="pendingPurchases.length" :content="pendingPurchases.length" color="warning" inline /></v-tab>
        <v-tab value="settings">Settings</v-tab>
        <v-tab value="activity">Activity</v-tab>
      </v-tabs>

      <!-- Members -->
      <template v-if="tab === 'members'">
        <AxoDataTable :headers="memberHeaders" :items="members" item-value="user_id" title="Members"
                      help="Owner: everything. Admin: invites, manages the team's VEs, sees spending. Member: uses what the team owns. Only the Owner makes or removes Admins; the Owner changes only by transferring the team."
                      :searchable="members.length > 10" :pending="(m) => busy === m.user_id">
          <template #item.username="{ item }">
            <div class="d-flex align-center ga-3 py-2">
              <v-avatar size="32" color="primary" variant="tonal"><span class="text-caption font-weight-bold">{{ initials(item.username) }}</span></v-avatar>
              <span class="font-weight-medium">{{ item.username ?? item.user_id }}</span>
              <v-chip v-if="item.user_id === me.me?.user_id" size="x-small" variant="outlined">you</v-chip>
            </div>
          </template>
          <template #item.role="{ item }">
            <v-select v-if="mayChangeRole(item)" :model-value="item.role" :items="roleChoices" density="compact" variant="outlined"
                      hide-details style="max-width: 150px" @update:model-value="(r) => changeRole(item, r)" />
            <v-chip v-else size="small" variant="tonal" :color="TEAM_ROLE_INFO[item.role as TeamRole]?.color">{{ teamRoleTitle(item.role) }}</v-chip>
          </template>
          <template #item.tools="{ item }">
            <v-btn v-if="item.user_id === me.me?.user_id && item.role !== 'owner'" size="small" variant="text" color="error"
                   class="text-none" @click="remove(item)">Leave</v-btn>
            <v-btn v-else-if="mayRemove(item)" size="small" variant="text" class="text-none" @click="remove(item)">Remove</v-btn>
          </template>
        </AxoDataTable>

        <AxoPanel v-if="isAdmin" title="Invitations waiting" icon="mdi-email-fast-outline" stacked flush class="mt-6"
                  help="People invited who haven't answered yet. They join when they accept.">
          <template #actions><v-btn size="small" variant="text" color="primary" class="text-none" prepend-icon="mdi-plus" @click="inviteOpen = true">Invite</v-btn></template>
          <div v-if="!team.invitations?.length" class="px-4 pb-4 text-body-2 axo-muted">Nobody is waiting to answer.</div>
          <v-table v-else density="comfortable">
            <tbody>
              <tr v-for="inv in team.invitations" :key="inv.id">
                <td><span class="font-weight-medium">{{ inv.invitee.name }}</span>
                  <span v-if="inv.invitee.type === 'email'" class="text-caption axo-muted"> · by email</span></td>
                <td>{{ teamRoleTitle(inv.role) }}</td>
                <td class="text-caption axo-muted">{{ inv.invited_by_name }}, {{ timeAgo(inv.created_at) }}</td>
                <td class="text-end"><v-btn variant="text" size="small" class="text-none" @click="withdraw(inv)">Cancel</v-btn></td>
              </tr>
            </tbody>
          </v-table>
        </AxoPanel>
      </template>

      <!-- VEs and buckets -->
      <template v-else-if="tab === 'resources'">
        <v-row>
          <v-col v-for="kind in (['ve', 'bucket'] as const)" :key="kind" cols="12" md="6">
            <AxoPanel :title="kind === 've' ? 'VEs' : 'Buckets'" :icon="kind === 've' ? 'mdi-cube-outline' : 'mdi-database-outline'" flush
                      :help="kind === 've' ? 'Owned: the team pays for them. Shared: someone gave the team a role in theirs. Members get at least Viewer on owned VEs; Admins are Admin there.' : 'Owned: the team pays for what they store. Shared: someone gave the team access to theirs.'">
              <template #actions>
                <v-btn v-if="kind === 've' && isAdmin" size="small" variant="text" color="primary" class="text-none"
                       prepend-icon="mdi-plus" :to="`/ves/new?team=${team.name}`">Rent a VE for this team</v-btn>
                <v-btn v-else-if="kind === 'bucket' && isAdmin" size="small" variant="text" color="primary" class="text-none"
                       prepend-icon="mdi-plus" to="/buckets">New bucket</v-btn>
              </template>
              <AxoEmptyState v-if="!ofKind(kind).length" :icon="kind === 've' ? 'mdi-cube-outline' : 'mdi-database-outline'"
                             :title="kind === 've' ? 'No VEs' : 'No buckets'"
                             :text="kind === 've' ? 'Rent a VE owned by the team, or ask someone to share theirs with it.' : 'Create a bucket owned by the team, or ask someone to share theirs.'" />
              <v-list v-else density="comfortable" class="py-0">
                <v-list-item v-for="r in ofKind(kind)" :key="r.id" :to="resourceLink(r)">
                  <v-list-item-title class="font-weight-medium">{{ r.name }}</v-list-item-title>
                  <v-list-item-subtitle>
                    <span v-if="r.owned">Owned by the team</span>
                    <span v-else>Shared by {{ r.shared_by ?? 'someone' }}</span>
                    · team's role {{ resourceRoleTitle(r) }}
                  </v-list-item-subtitle>
                  <template #append>
                    <v-chip size="x-small" :variant="r.owned ? 'flat' : 'outlined'" :color="r.owned ? 'primary' : undefined">
                      {{ r.owned ? 'Owned' : 'Shared' }}</v-chip>
                  </template>
                </v-list-item>
              </v-list>
            </AxoPanel>
          </v-col>
        </v-row>
      </template>

      <!-- Spending -->
      <template v-else-if="tab === 'spending' && isAdmin">
        <AxoErrorAlert v-if="spendingError" :error="spendingError" title="Spending couldn't load" :retry="loadSpending" class="mb-4" />
        <div class="d-flex mb-3">
          <v-spacer />
          <v-btn variant="tonal" color="primary" class="text-none" prepend-icon="mdi-receipt-text-outline"
                 :to="`/billing?team=${team?.team_id}`">The team's bills</v-btn>
        </div>
        <v-row v-if="spending">
          <v-col cols="12" md="5">
            <AxoPanel :title="`This month (${spending.month})`" icon="mdi-cash-multiple"
                      help="Plan fees of the team's VEs charged this calendar month: new VEs, renewals and upgrades, by who bought them. Usage (jobs, storage) is on each VE's bill.">
              <div class="axo-big mb-1">{{ formatMoney(spending.team_cents) }}</div>
              <div v-if="spending.limits?.team_cents != null" class="mb-3">
                <v-progress-linear :model-value="pct(spending.team_cents, spending.limits.team_cents)" height="6" rounded
                                   :color="pct(spending.team_cents, spending.limits.team_cents) >= 100 ? 'error' : pct(spending.team_cents, spending.limits.team_cents) >= 80 ? 'warning' : 'primary'" />
                <div class="text-caption axo-muted mt-1">of {{ formatMoney(spending.limits.team_cents) }} team limit</div>
              </div>
              <div v-else class="text-caption axo-muted mb-3">No team limit</div>
              <v-table density="compact">
                <tbody>
                  <tr v-for="row in spendingRows" :key="row.user_id">
                    <td>{{ row.name }}</td>
                    <td class="text-end">{{ formatMoney(row.cents) }}
                      <span v-if="spending.limits?.per_admin_cents != null" class="text-caption axo-muted"> / {{ formatMoney(spending.limits.per_admin_cents) }}</span></td>
                  </tr>
                  <tr v-if="!spendingRows.length"><td colspan="2" class="axo-muted">Nothing bought this month.</td></tr>
                </tbody>
              </v-table>
            </AxoPanel>
          </v-col>
          <v-col cols="12" md="7">
            <AxoPanel title="Spending limits" icon="mdi-speedometer"
                      help="Monthly caps on plan fees. A purchase over a limit isn't refused: it waits for the Team Owner's approval.">
              <template v-if="isOwner">
                <div class="d-flex flex-wrap ga-4">
                  <LimitField v-model="limitsForm.team_cents" label="Whole team, per month" />
                  <LimitField v-model="limitsForm.per_admin_cents" label="Each person, per month" />
                </div>
                <div class="d-flex justify-end mt-2">
                  <AxoBtn :disabled="!limitsChanged" :loading="saving === 'limits'" @click="saveLimits">Save limits</AxoBtn>
                </div>
              </template>
              <div v-else class="text-body-2">
                <div>Whole team: <b>{{ spending.limits?.team_cents == null ? 'no limit' : formatMoney(spending.limits.team_cents) }}</b></div>
                <div>Each person: <b>{{ spending.limits?.per_admin_cents == null ? 'no limit' : formatMoney(spending.limits.per_admin_cents) }}</b></div>
                <div class="text-caption axo-muted mt-2">Only the Team Owner changes the limits.</div>
              </div>
            </AxoPanel>
          </v-col>
          <v-col cols="12">
            <AxoPanel title="Purchases waiting for approval" icon="mdi-cart-outline" flush
                      help="VEs someone tried to rent for the team over a limit. Only the Team Owner approves or rejects them; approving rents the VE at once.">
              <AxoEmptyState v-if="!pendingPurchases.length" icon="mdi-cart-check" title="Nothing waiting"
                             text="Purchases over a limit wait here for the Owner." />
              <div v-for="p in pendingPurchases" :key="p.purchase_id" class="axo-line">
                <div class="flex-grow-1">
                  <div><b>{{ p.buyer_name }}</b> wants to rent <b>{{ p.request.name }}</b> ({{ planTitle(p.plan_id) }}, {{ formatMoney(p.fee_cents) }} a month)</div>
                  <div class="text-caption axo-muted">{{ timeAgo(p.created_at) }}</div>
                </div>
                <div v-if="isOwner" class="d-flex ga-2">
                  <AxoBtn size="small" :loading="saving === p.purchase_id" @click="answerPurchase(p, true)">Approve</AxoBtn>
                  <v-btn size="small" variant="text" class="text-none" :disabled="saving === p.purchase_id" @click="answerPurchase(p, false)">Reject</v-btn>
                </div>
                <span v-else class="text-caption axo-muted">Waiting for the Owner</span>
              </div>
            </AxoPanel>
          </v-col>
        </v-row>
        <AxoLoader v-else-if="!spendingError" block />
      </template>

      <!-- Settings -->
      <template v-else-if="tab === 'settings'">
        <AxoPanel title="About the team" icon="mdi-information-outline" stacked class="mb-6">
          <v-textarea v-model="description" label="Description" rows="2" auto-grow :readonly="!isAdmin"
                      :hint="isAdmin ? 'What the team is for.' : 'Owners and Admins change it.'" persistent-hint />
          <div v-if="isAdmin" class="d-flex justify-end mt-2">
            <AxoBtn :disabled="description === (team.description ?? '')" :loading="saving === 'description'" @click="saveDescription">Save</AxoBtn>
          </div>
        </AxoPanel>

        <AxoPanel v-if="isOwner" title="Hand over the team" icon="mdi-swap-horizontal" stacked class="mb-6"
                  help="Another member becomes the Owner when they accept. You stay in the team as an Admin.">
          <div v-if="pendingTeamTransfer" class="d-flex align-center flex-wrap ga-3">
            <span class="text-body-2">Waiting for <b>{{ pendingTeamTransfer.to.name }}</b> to accept.</span>
            <v-btn size="small" variant="text" class="text-none" :loading="saving === 'transfer'" @click="cancelTransfer">Cancel</v-btn>
          </div>
          <div v-else class="d-flex flex-wrap align-start ga-3">
            <v-select v-model="transferTo" :items="transferChoices" label="New Owner" style="max-width: 280px"
                      :no-data-text="'Invite someone first: the new Owner must be a member'" hide-details="auto" />
            <AxoBtn :disabled="!transferTo" :loading="saving === 'transfer'" @click="startTransfer">Ask them</AxoBtn>
          </div>
        </AxoPanel>

        <AxoPanel v-if="isOwner" title="Delete the team" icon="mdi-alert-outline" stacked class="axo-danger">
          <div class="text-body-2 mb-3">
            Removes the team and everyone's access through it.
            <span v-if="owned('ve').length || owned('bucket').length" class="text-warning">
              It still owns {{ plural(owned('ve').length, 'VE') }} and {{ plural(owned('bucket').length, 'bucket') }}:
              delete them or hand them over first.</span>
          </div>
          <v-btn color="error" variant="tonal" class="text-none" :disabled="!!(owned('ve').length || owned('bucket').length)"
                 :loading="saving === 'delete'" @click="deleteTeam">Delete team</v-btn>
        </AxoPanel>
        <div v-if="!isOwner" class="text-body-2 axo-muted mt-2">Only the Team Owner hands over or deletes the team.</div>
      </template>

      <!-- Activity -->
      <template v-else-if="tab === 'activity'">
        <AxoPanel title="History" icon="mdi-history" stacked flush help="Who joined, left, changed roles and settings -- newest first.">
          <AxoErrorAlert v-if="historyError" :error="historyError" title="The history couldn't load" :retry="loadHistory" class="ma-4" />
          <AxoLoader v-else-if="history === null" block />
          <AxoEmptyState v-else-if="!history.length" icon="mdi-history" title="Nothing yet" />
          <v-list v-else density="compact" class="py-0">
            <v-list-item v-for="(h, i) in history" :key="i" :prepend-icon="teamHistoryIcon(h)">
              <v-list-item-title class="text-body-2" style="white-space: normal">{{ teamHistoryText(h) }}</v-list-item-title>
              <template #append><span class="text-caption axo-muted" :title="formatDateTime(h.at)">{{ timeAgo(h.at) }}</span></template>
            </v-list-item>
          </v-list>
        </AxoPanel>
      </template>

      <TeamInviteDialog v-model="inviteOpen" :team="team" :my-role="myRole" @invited="(inv) => team!.invitations = [...(team!.invitations ?? []), inv]" />
    </template>
  </div>
</template>

<script setup lang="ts">
import type { Invitation, Team, TeamMember, TeamResource, TeamRole, Transfer } from '../../utils/teams'
import type { TeamHistoryRow } from '../../utils/teamHistory'

// One team: its members (roles, invitations), what it owns and what's shared
// with it, its spending (Owner + Admins; limits and approvals: Owner only),
// settings (description, hand-over, delete) and history.
const route = useRoute()
const me = useMeStore()
const notify = useNotifier()
const confirmDialog = useConfirm()
const { apiFetch } = useApi()
const inbox = useInboxFollow()

const name = computed(() => String(route.params.name))
useHead({ title: () => `${name.value} · Teams · axo` })

const TABS = ['members', 'resources', 'spending', 'settings', 'activity']
const tab = ref(TABS.includes(String(route.query.tab)) ? String(route.query.tab) : 'members')
watch(tab, (t) => {
  navigateTo({ query: t === 'members' ? {} : { tab: t } }, { replace: true })
  if (t === 'spending') loadSpending()
  if (t === 'activity') loadHistory()
})

const team = ref<Team | null>(null)
const resources = ref<TeamResource[]>([])
const loading = ref(true)
const error = ref<unknown>(null)

async function load() {
  error.value = null
  try {
    const mine = await apiFetch<Team[]>('/teams')
    const found = mine.find(t => t.name === name.value)
    if (!found) throw new AxoApiError({ error: 'TEAM_NOT_FOUND', message: `You're not in a team called ${name.value}` }, 404)
    const [detail, res] = await Promise.all([
      apiFetch<Team>(`/teams/${found.team_id}`), apiFetch<TeamResource[]>(`/teams/${found.team_id}/resources`),
    ])
    team.value = detail
    resources.value = res
    description.value = detail.description ?? ''
    if (tab.value === 'spending') loadSpending()
    if (tab.value === 'activity') loadHistory()
  } catch (err) { error.value = err } finally { loading.value = false }
}
watch(name, () => { loading.value = true; load() })
onMounted(load)

const myRole = computed<TeamRole>(() => (team.value?.my_role ?? 'member') as TeamRole)
const isAdmin = computed(() => teamRoleAtLeast(myRole.value, 'admin'))
const isOwner = computed(() => myRole.value === 'owner')
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
const initials = (s?: string | null) => (s ?? '?').slice(0, 2).toUpperCase()
const owned = (kind: 've' | 'bucket') => resources.value.filter(r => r.kind === kind && r.owned)
const ofKind = (kind: 've' | 'bucket') => resources.value.filter(r => r.kind === kind)
  .sort((a, b) => Number(b.owned) - Number(a.owned) || a.name.localeCompare(b.name))

// ── members ────────────────────────────────────────────────────────────────
const ORDER: Record<string, number> = { owner: 0, admin: 1, member: 2 }
const members = computed(() => [...(team.value?.members ?? [])]
  .sort((a, b) => ORDER[a.role]! - ORDER[b.role]! || String(a.username).localeCompare(String(b.username))))
const memberHeaders = [
  { title: 'Who', key: 'username' }, { title: 'Role', key: 'role', sortable: false },
  { title: '', key: 'tools', sortable: false, align: 'end' as const },
]
const roleChoices = [{ title: 'Member', value: 'member' }, { title: 'Admin', value: 'admin' }]
const mayChangeRole = (m: TeamMember) => m.role !== 'owner' && m.user_id !== me.me?.user_id
  && (isOwner.value || (isAdmin.value && m.role === 'member'))
const mayRemove = (m: TeamMember) => m.role !== 'owner' && m.user_id !== me.me?.user_id
  && (isOwner.value || (isAdmin.value && m.role === 'member'))

const busy = ref<string | null>(null)
async function changeRole(m: TeamMember, role: string) {
  busy.value = m.user_id
  const before = m.role
  m.role = role as TeamRole
  try {
    await apiFetch(`/teams/${team.value!.team_id}/members/${m.user_id}`, { method: 'PUT', body: { role } })
    notify.success(`${m.username} is now ${teamRoleTitle(role)}`)
  } catch (err) { m.role = before; notify.error(err, "The role couldn't be changed") } finally { busy.value = null }
}

async function remove(m: TeamMember) {
  const leaving = m.user_id === me.me?.user_id
  const lose = [
    owned('ve').length ? `Access to the team's ${plural(owned('ve').length, 'VE')}: ${owned('ve').map(r => r.name).join(', ')}` : '',
    owned('bucket').length ? `Access to the team's ${plural(owned('bucket').length, 'bucket')}: ${owned('bucket').map(r => r.name).join(', ')}` : '',
    resources.value.some(r => !r.owned) ? `What's shared with the team (${resources.value.filter(r => !r.owned).map(r => r.name).join(', ')})` : '',
  ].filter(Boolean)
  if (!await confirmDialog.confirm({
    title: leaving ? `Leave ${team.value!.name}?` : `Remove ${m.username} from ${team.value!.name}?`, danger: true,
    confirmText: leaving ? 'Leave the team' : 'Remove',
    text: lose.length ? (leaving ? "You'll lose, at once:" : 'They lose, at once:')
      : `The team owns nothing and has nothing shared with it, so ${leaving ? 'you lose' : 'they lose'} no access.`,
    consequences: lose,
  })) return
  busy.value = m.user_id
  try {
    await apiFetch(`/teams/${team.value!.team_id}/members/${m.user_id}`, { method: 'DELETE' })
    if (leaving) {
      notify.success(`You left ${team.value!.name}`)
      await me.load(true)
      await navigateTo('/teams')
      return
    }
    team.value!.members = team.value!.members.filter(x => x.user_id !== m.user_id)
    notify.success(`${m.username} was removed`)
  } catch (err) { notify.error(err, leaving ? "You couldn't leave the team" : "They couldn't be removed") } finally { busy.value = null }
}

const inviteOpen = ref(false)
async function withdraw(inv: Invitation) {
  try {
    await apiFetch(`/invitations/${inv.id}`, { method: 'DELETE' })
    team.value!.invitations = team.value!.invitations!.filter(i => i.id !== inv.id)
    notify.success('Invitation cancelled')
  } catch (err) { notify.error(err, "The invitation couldn't be cancelled") }
}

// ── spending ───────────────────────────────────────────────────────────────
interface Spending { month: string; team_cents: number; by_buyer_cents: Record<string, number>; limits: { per_admin_cents?: number | null; team_cents?: number | null } }
interface Purchase { purchase_id: string; buyer_name: string; plan_id: string; fee_cents: number; request: { name: string }; created_at: number; status: string }
const spending = ref<Spending | null>(null)
const purchases = ref<Purchase[]>([])
const spendingError = ref<unknown>(null)
const pendingPurchases = computed(() => purchases.value.filter(p => p.status === 'pending'))
const limitsForm = reactive<{ team_cents: number | null; per_admin_cents: number | null }>({ team_cents: null, per_admin_cents: null })
const limitsChanged = computed(() => spending.value && (limitsForm.team_cents !== (spending.value.limits?.team_cents ?? null)
  || limitsForm.per_admin_cents !== (spending.value.limits?.per_admin_cents ?? null)))

async function loadSpending() {
  if (!team.value || !isAdmin.value) return
  spendingError.value = null
  try {
    const [s, p] = await Promise.all([
      apiFetch<Spending>(`/teams/${team.value.team_id}/spending`),
      apiFetch<Purchase[]>(`/teams/${team.value.team_id}/purchases`, { query: { status: 'pending' } }),
    ])
    spending.value = s
    purchases.value = p
    limitsForm.team_cents = s.limits?.team_cents ?? null
    limitsForm.per_admin_cents = s.limits?.per_admin_cents ?? null
  } catch (err) { spendingError.value = err }
}
const spendingRows = computed(() => Object.entries(spending.value?.by_buyer_cents ?? {})
  .map(([user_id, cents]) => ({ user_id, cents, name: team.value?.members.find(m => m.user_id === user_id)?.username ?? 'A former member' }))
  .sort((a, b) => b.cents - a.cents))
const pct = (v: number, of: number) => (of > 0 ? Math.min(100, (v / of) * 100) : 100)
const planTitle = (id: string) => (id ? id[0]!.toUpperCase() + id.slice(1) : '—')

const saving = ref<string | null>(null)
async function saveLimits() {
  saving.value = 'limits'
  try {
    await apiFetch(`/teams/${team.value!.team_id}`, { method: 'PATCH', body: { ...limitsForm } })
    notify.success('Spending limits saved')
    await loadSpending()
  } catch (err) { notify.error(err, "The limits couldn't be saved") } finally { saving.value = null }
}
async function answerPurchase(p: Purchase, approve: boolean) {
  if (approve && !await confirmDialog.confirm({
    title: `Rent ${p.request.name} for the team?`, confirmText: 'Approve',
    text: `${formatMoney(p.fee_cents)} is charged to the team now, and every month after.`,
  })) return
  saving.value = p.purchase_id
  try {
    await apiFetch(`/purchases/${p.purchase_id}/${approve ? 'approve' : 'reject'}`, { method: 'POST' })
    notify.success(approve ? `${p.request.name} is being rented for the team` : 'Purchase rejected')
    await Promise.all([loadSpending(), approve ? load() : Promise.resolve()])
  } catch (err) { notify.error(err, "The purchase couldn't be answered") } finally { saving.value = null }
}

// ── settings ───────────────────────────────────────────────────────────────
const description = ref('')
async function saveDescription() {
  saving.value = 'description'
  try {
    const t = await apiFetch<Team>(`/teams/${team.value!.team_id}`, { method: 'PATCH', body: { description: description.value } })
    team.value!.description = t.description
    notify.success('Description saved')
  } catch (err) { notify.error(err, "The description couldn't be saved") } finally { saving.value = null }
}

const transferTo = ref<string | null>(null)
const transferChoices = computed(() => (team.value?.members ?? []).filter(m => m.role !== 'owner' && m.username)
  .map(m => ({ title: `${m.username} (${teamRoleTitle(m.role)})`, value: m.username! })))
const pendingTeamTransfer = computed<Transfer | undefined>(() => inbox.transfersOut.value
  .find(t => t.kind === 'team' && t.target_id === team.value?.team_id))
async function startTransfer() {
  if (!await confirmDialog.confirm({
    title: `Hand ${team.value!.name} over to ${transferTo.value}?`, confirmText: 'Ask them',
    text: 'When they accept, they become the Owner (limits, approvals, deleting the team) and you become an Admin.',
  })) return
  saving.value = 'transfer'
  try {
    await apiFetch('/transfers', { method: 'POST', body: { kind: 'team', target: team.value!.team_id, to: transferTo.value } })
    notify.success(`${transferTo.value} was asked to take over the team`)
    transferTo.value = null
    await inbox.load()
  } catch (err) { notify.error(err, "The hand-over couldn't be asked for") } finally { saving.value = null }
}
async function cancelTransfer() {
  saving.value = 'transfer'
  try {
    await apiFetch(`/transfers/${pendingTeamTransfer.value!.id}`, { method: 'DELETE' })
    notify.success("You're keeping the team")
    await inbox.load()
  } catch (err) { notify.error(err, "The hand-over couldn't be cancelled") } finally { saving.value = null }
}

async function deleteTeam() {
  if (!await confirmDialog.confirm({
    title: `Delete ${team.value!.name}?`, danger: true, confirmText: 'Delete team', typeToConfirm: team.value!.name,
    text: "Everyone loses what they had through the team. This can't be undone.",
    consequences: [`${plural(team.value!.members.length, 'member')} leave it`, 'Its name becomes free for anyone to use'],
  })) return
  saving.value = 'delete'
  try {
    await apiFetch(`/teams/${team.value!.team_id}`, { method: 'DELETE' })
    notify.success(`${team.value!.name} was deleted`)
    await me.load(true)
    await navigateTo('/teams')
  } catch (err) { notify.error(err, "The team couldn't be deleted") } finally { saving.value = null }
}

// ── activity ───────────────────────────────────────────────────────────────
const history = ref<TeamHistoryRow[] | null>(null)
const historyError = ref<unknown>(null)
async function loadHistory() {
  if (!team.value) return
  historyError.value = null
  try { history.value = await apiFetch<TeamHistoryRow[]>(`/teams/${team.value.team_id}/history`, { query: { limit: 200 } }) } catch (err) { historyError.value = err }
}
</script>

<style scoped>
.axo-line { display: flex; align-items: center; gap: 12px; padding: 12px 16px; flex-wrap: wrap; }
.axo-line + .axo-line { border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
.axo-big { font-size: 1.75rem; font-weight: 700; line-height: 1.1; letter-spacing: -0.01em; }
.axo-danger { border: 1px solid rgba(var(--v-theme-error), 0.4) !important; }
</style>
