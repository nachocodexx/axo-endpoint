<template>
  <v-menu v-model="open" location="bottom end" :close-on-content-click="false" width="400" max-height="560">
    <template #activator="{ props: menu }">
      <v-btn v-bind="menu" icon variant="text" aria-label="Notifications">
        <v-badge :model-value="store.unread > 0" :content="store.unread > 99 ? '99+' : store.unread" color="error">
          <v-icon :class="{ 'axo-bell-ring': ringing }">mdi-bell-outline</v-icon>
        </v-badge>
      </v-btn>
    </template>
    <v-card border>
      <div class="d-flex align-center px-4 py-3">
        <span class="text-subtitle-1 font-weight-medium">Notifications</span>
        <v-spacer />
        <v-btn v-if="store.unread" size="small" variant="text" color="primary" @click="store.markRead()">
          Mark all as read
        </v-btn>
      </div>
      <v-divider />
      <AxoLoader v-if="loading" block :size="32" />
      <AxoEmptyState v-else-if="!store.items.length" icon="mdi-bell-check-outline" title="You're all caught up"
                     text="Invitations, bills, VE changes and finished jobs show up here." />
      <v-list v-else density="compact" class="py-0" style="max-height: 460px; overflow-y: auto">
        <v-list-item v-for="n in store.items" :key="n.notification_id" :class="{ 'axo-unread': !n.read }"
                     class="py-2" @click="go(n)">
          <template #prepend>
            <v-icon :color="n.read ? 'muted' : 'primary'">{{ NOTIFICATION_ICONS[n.kind] ?? 'mdi-bell-outline' }}</v-icon>
          </template>
          <div class="text-body-2" style="white-space: normal">{{ noticeText(n) }}</div>
          <div class="text-caption axo-muted">{{ timeAgo(n.created_at) }}</div>
          <div v-if="n.kind === 'invitation_received' && n.data?.invitation_id" class="d-flex align-center ga-2 mt-2" @click.stop>
            <template v-if="!invitations.answers.value[n.data.invitation_id]">
              <v-btn size="small" color="primary" variant="flat" class="text-none" :loading="answering === n.data.invitation_id"
                     @click="reply(n, true)">Accept</v-btn>
              <v-btn size="small" variant="text" class="text-none" :disabled="answering === n.data.invitation_id"
                     @click="reply(n, false)">Decline</v-btn>
            </template>
            <span v-else class="text-caption" :class="invitations.answers.value[n.data.invitation_id] === 'accepted' ? 'text-success' : 'axo-muted'">
              <v-icon size="14">{{ invitations.answers.value[n.data.invitation_id] === 'accepted' ? 'mdi-check' : 'mdi-minus' }}</v-icon>
              {{ ANSWER_TITLES[invitations.answers.value[n.data.invitation_id]!] }}
            </span>
          </div>
          <div v-else-if="n.kind === 'transfer_requested'" class="mt-2" @click.stop>
            <v-btn size="small" variant="tonal" color="primary" class="text-none" @click="go(n)">Review</v-btn>
          </div>
        </v-list-item>
      </v-list>
      <v-divider />
      <v-btn block variant="text" color="primary" class="text-none" to="/notifications" @click="open = false">See all</v-btn>
    </v-card>
  </v-menu>
</template>

<script setup lang="ts">
import type { Notice } from '../types/cp'

// The bell: every notice, newest first, with the unread count. Clicking a
// notice marks it read and goes to its subject.
const store = useNotificationsStore()
const open = ref(false)
const loading = ref(false)
const ringing = ref(false)

onMounted(async () => {
  if (store.loaded) return
  loading.value = true
  try { await store.load() } catch { /* the bell stays empty; pages show their own errors */ } finally { loading.value = false }
})

watch(() => store.unread, (now, before) => {
  if (now > before) { ringing.value = true; setTimeout(() => (ringing.value = false), 900) }
})

// An invitation is answered right here; a transfer (it involves money) is
// reviewed in the inbox.
const invitations = useInvitations()
const answering = ref<string | null>(null)
const ANSWER_TITLES = { accepted: 'Accepted', rejected: 'Declined', gone: 'Already answered' }
async function reply(n: Notice, accept: boolean) {
  answering.value = n.data.invitation_id
  if (!n.read) store.markRead([n.notification_id])
  try { await invitations.answer(n.data.invitation_id, accept) } catch { /* the notifier said why */ } finally { answering.value = null }
}

function go(n: Notice) {
  if (!n.read) store.markRead([n.notification_id])
  const to = notificationLink(n)
  open.value = false
  if (to) navigateTo(to)
}
</script>

<style scoped>
.axo-unread { background: rgba(0, 134, 255, 0.06); }
.axo-bell-ring { animation: axo-ring 0.9s ease; transform-origin: top center; }
@keyframes axo-ring {
  0%, 100% { transform: rotate(0); } 15% { transform: rotate(14deg); } 30% { transform: rotate(-12deg); }
  45% { transform: rotate(8deg); } 60% { transform: rotate(-6deg); } 75% { transform: rotate(3deg); }
}
</style>
