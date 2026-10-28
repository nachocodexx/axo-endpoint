<template>
  <div>
    <AxoPageHeader title="Notifications" icon="mdi-bell-outline"
                   subtitle="Everything the bell told you: invitations, VE changes, finished jobs and bills."
                   :help="`Clicking one opens what it's about and marks it read. Notifications are deleted ${KEEP_DAYS} days after they arrive.`">
      <template #actions>
        <v-btn variant="text" class="text-none" prepend-icon="mdi-check-all" :disabled="!unreadShown" :loading="marking" @click="markAll">
          Mark all as read
        </v-btn>
      </template>
    </AxoPageHeader>

    <div class="d-flex flex-wrap align-center ga-3 mb-4">
      <v-btn-toggle v-model="show" mandatory density="compact" variant="outlined" divided color="primary">
        <v-btn value="all" class="text-none">All</v-btn>
        <v-btn value="unread" class="text-none">Unread<v-badge v-if="store.unread" :content="store.unread" color="error" inline /></v-btn>
      </v-btn-toggle>
      <v-chip-group v-model="groups" multiple column selected-class="text-primary">
        <v-chip v-for="g in NOTIFICATION_GROUPS" :key="g.value" :value="g.value" :prepend-icon="g.icon" filter variant="outlined" size="small">
          {{ g.title }}
        </v-chip>
      </v-chip-group>
    </div>

    <AxoErrorAlert v-if="error" :error="error" title="Your notifications couldn't load" :retry="reload" class="mb-4" />
    <v-card class="axo-notes">
      <AxoLoader v-if="loading && !items.length" block />
      <AxoEmptyState v-else-if="!items.length" icon="mdi-bell-check-outline"
                     :title="show === 'unread' ? `You're all caught up` : 'Nothing here'"
                     :text="groups.length ? 'No notifications of these types.' : 'Invitations, bills, VE changes and finished jobs show up here.'" />
      <template v-for="day in days" :key="day.title">
        <div class="axo-day text-caption font-weight-medium axo-muted px-5 py-2">{{ day.title }}</div>
        <div v-for="n in day.items" :key="n.notification_id" class="axo-note" :class="{ 'axo-note--unread': !n.read }"
             role="button" tabindex="0" @click="open(n)" @keydown.enter="open(n)">
          <v-icon :color="n.read ? undefined : 'primary'" class="mt-1 flex-shrink-0" :class="{ 'axo-muted': n.read }">
            {{ NOTIFICATION_ICONS[n.kind] ?? 'mdi-bell-outline' }}</v-icon>
          <div class="flex-grow-1 min-w-0">
            <div class="text-body-2" :class="{ 'font-weight-medium': !n.read }">{{ noticeText(n) }}</div>
            <div class="text-caption axo-muted" :title="formatExactTime(n.created_at)">{{ formatTime(n.created_at) }} · {{ timeAgo(n.created_at) }}</div>
            <div v-if="n.kind === 'invitation_received' && n.data?.invitation_id" class="d-flex align-center ga-2 mt-2" @click.stop>
              <template v-if="!invitations.answers.value[n.data.invitation_id]">
                <v-btn size="small" color="primary" variant="flat" class="text-none" :loading="answering === n.data.invitation_id"
                       @click="reply(n, true)">Accept</v-btn>
                <v-btn size="small" variant="text" class="text-none" :disabled="answering === n.data.invitation_id"
                       @click="reply(n, false)">Decline</v-btn>
              </template>
              <span v-else class="text-caption" :class="invitations.answers.value[n.data.invitation_id] === 'accepted' ? 'text-success' : 'axo-muted'">
                {{ ANSWER_TITLES[invitations.answers.value[n.data.invitation_id]!] }}
              </span>
            </div>
            <div v-else-if="n.kind === 'transfer_requested'" class="mt-2" @click.stop>
              <v-btn size="small" variant="tonal" color="primary" class="text-none" @click="open(n)">Review</v-btn>
            </div>
          </div>
          <span v-if="!n.read" class="axo-dot flex-shrink-0" aria-label="Unread" />
        </div>
      </template>
      <div v-if="more" class="d-flex justify-center pa-4">
        <v-btn variant="tonal" class="text-none" :loading="loading" @click="loadMore">Load more</v-btn>
      </div>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import type { Notice } from '../types/cp'

// Every notice (GET /notifications), newest first, grouped by day: All |
// Unread and the type chips (the Control Plane's groups), "Load more"
// pages back, "Mark all as read" follows the filter. Clicking one opens
// its subject (as the bell does); invitations are answered right here.
useHead({ title: 'Notifications · axo' })
const { apiFetch } = useApi()
const store = useNotificationsStore()
const invitations = useInvitations()
const notify = useNotifier()
const route = useRoute()
const KEEP_DAYS = 90
const PAGE = 50
const ANSWER_TITLES = { accepted: 'Accepted', rejected: 'Declined', gone: 'Already answered' }

const show = ref<'all' | 'unread'>(route.query.show === 'unread' ? 'unread' : 'all')
const groups = ref<string[]>(String(route.query.groups ?? '').split(',').filter(g => NOTIFICATION_GROUPS.some(x => x.value === g)))
const items = ref<Notice[]>([])
const more = ref(false)
const loading = ref(false)
const error = ref<unknown>(null)
let request = 0

async function fetchPage(before?: Notice) {
  const mine = ++request
  loading.value = true
  error.value = null
  try {
    const answer = await apiFetch<{ unread: number; items: Notice[] }>('/notifications', { query: {
      limit: PAGE, unread_only: show.value === 'unread' || undefined, groups: groups.value.join(',') || undefined,
      before: before?.created_at, before_id: before?.notification_id,
    } })
    if (mine !== request) return
    items.value = before ? [...items.value, ...answer.items] : answer.items
    more.value = answer.items.length === PAGE
    store.unread = answer.unread
  } catch (err) {
    if (mine === request) error.value = err
  } finally {
    if (mine === request) loading.value = false
  }
}
const reload = () => fetchPage()
const loadMore = () => fetchPage(items.value[items.value.length - 1])
onMounted(reload)
watch([show, groups], () => {
  const query = { ...route.query, show: show.value === 'unread' ? 'unread' : undefined, groups: groups.value.join(',') || undefined }
  const url = useRouter().resolve({ path: route.path, query }).href
  history.replaceState(history.state, '', url)
  items.value = []
  reload()
}, { deep: true })

// A new notice from the live channel (the bell's store) goes on top when it fits the filter.
function fits(n: Notice) {
  if (show.value === 'unread' && n.read) return false
  return !groups.value.length || groups.value.some(g => kindsOf(g).includes(n.kind))
}
const KINDS: Record<string, string[]> = {
  people: ['invitation_received', 'invitation_answered', 'transfer_requested', 'transfer_completed', 'team_purchase',
    'purchase_pending', 'purchase_answered'],
  ves: ['ve_state', 'rental_ending', 'plan_change_scheduled'],
  jobs: ['job_finished'],
  billing: ['bill_closed', 'renewal_soon', 'budget_warning', 'limit_warning'],
}
const kindsOf = (g: string) => KINDS[g] ?? []
watch(() => store.items[0]?.notification_id, () => {
  const n = store.items[0]
  if (n && fits(n) && !items.value.some(x => x.notification_id === n.notification_id)) items.value = [n, ...items.value]
})

// Grouped by day in the person's own time zone: Today, Yesterday, then the date.
const days = computed(() => {
  const today = formatDate(Date.now() / 1000)
  const yesterday = formatDate(Date.now() / 1000 - 86400)
  const out: Array<{ title: string; items: Notice[] }> = []
  for (const n of items.value) {
    const d = formatDate(n.created_at)
    const title = d === today ? 'Today' : d === yesterday ? 'Yesterday' : d
    if (out[out.length - 1]?.title !== title) out.push({ title, items: [] })
    out[out.length - 1]!.items.push(n)
  }
  return out
})

const unreadShown = computed(() => (groups.value.length ? items.value.some(n => !n.read) : store.unread > 0))

async function markRead(ids: string[]) {
  const target = new Set(ids)
  items.value = items.value.map(n => (target.has(n.notification_id) ? { ...n, read: true } : n))
  try {
    await apiFetch('/notifications/read', { method: 'POST', body: { notification_ids: ids } })
  } catch { /* only a dot: the next load shows it again */ }
  store.load().catch(() => null)
}

function open(n: Notice) {
  if (!n.read) markRead([n.notification_id])
  const to = notificationLink(n)
  if (to) navigateTo(to)
}

const marking = ref(false)
async function markAll() {
  marking.value = true
  try {
    await apiFetch('/notifications/read', { method: 'POST', body: { groups: groups.value.length ? groups.value : null } })
    if (show.value === 'unread') items.value = []
    else items.value = items.value.map(n => (fits(n) ? { ...n, read: true } : n))
    await store.load()
  } catch (err) {
    notify.error(err, "Couldn't mark them as read")
  } finally {
    marking.value = false
  }
}

const answering = ref<string | null>(null)
async function reply(n: Notice, accept: boolean) {
  answering.value = n.data.invitation_id
  if (!n.read) markRead([n.notification_id])
  try { await invitations.answer(n.data.invitation_id, accept) } catch { /* the notifier said why */ } finally { answering.value = null }
}
</script>

<style scoped>
.axo-day { background: rgba(var(--v-theme-on-surface), 0.03); text-transform: uppercase; letter-spacing: 0.04em; }
.axo-note { display: flex; gap: 14px; align-items: flex-start; padding: 12px 20px; cursor: pointer;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
.axo-note:hover { background: rgba(var(--v-theme-on-surface), 0.04); }
.axo-note:focus-visible { outline: 2px solid rgb(var(--v-theme-primary)); outline-offset: -2px; }
.axo-note--unread { background: rgba(0, 134, 255, 0.06); }
.axo-dot { width: 8px; height: 8px; border-radius: 50%; background: rgb(var(--v-theme-primary)); margin-top: 8px; }
</style>
