<template>
  <div>
    <AxoPageHeader title="Teams" icon="mdi-account-group-outline"
                   subtitle="People who own and share VEs and buckets together."
                   help="A team owns VEs and buckets like a person does, and gives its members access to them. Owner: everything, including spending limits, approving purchases, transferring and deleting the team. Admin: invites members, is Admin of the team's VEs, sees its spending. Member: uses what the team owns (at least Viewer on its VEs) and whatever is shared with the team.">
      <template #actions>
        <AxoBtn v-if="!isGuest" prepend-icon="mdi-plus" @click="createOpen = true">New team</AxoBtn>
      </template>
    </AxoPageHeader>

    <v-alert v-if="isGuest" type="info" variant="tonal" density="compact" class="mb-4">
      Accounts made from an email invitation can't create or join teams. Sign up with your own account to use teams.
    </v-alert>

    <AxoPanel v-if="inbox.teamInvitations.value.length" title="Invitations to join" icon="mdi-email-plus-outline" stacked flush class="mb-6">
      <div v-for="inv in inbox.teamInvitations.value" :key="inv.id" class="axo-invite">
        <v-avatar size="36" class="axo-soft"><v-icon color="primary">mdi-account-group-outline</v-icon></v-avatar>
        <div class="flex-grow-1" style="min-width: 0">
          <div class="text-body-1"><b>{{ inv.invited_by_name }}</b> invited you to <b>{{ inv.target_name }}</b> as {{ teamRoleTitle(inv.role) }}</div>
          <div class="text-body-2 axo-muted">{{ acceptMeans(inv) }} · {{ timeAgo(inv.created_at) }}</div>
        </div>
        <div class="d-flex ga-2 flex-shrink-0">
          <AxoBtn size="small" :loading="busy === inv.id" @click="answer(inv.id, true)">Accept</AxoBtn>
          <v-btn size="small" variant="text" class="text-none" :disabled="busy === inv.id" @click="answer(inv.id, false)">Decline</v-btn>
        </div>
      </div>
    </AxoPanel>

    <AxoErrorAlert v-if="error" :error="error" title="Your teams couldn't load" :retry="load" class="mb-4" />

    <AxoDataTable :headers="headers" :items="teams" item-value="team_id" :loading="loading" title="Your teams"
                  :searchable="teams.length > 8" :row-to="(t) => `/teams/${t.name}`" :sort-by="[{ key: 'name', order: 'asc' }]">
      <template #empty>
        <AxoEmptyState icon="mdi-account-group-outline" title="No teams yet"
                       text="Teams let several people own and share VEs and buckets, with one bill and spending limits.">
          <AxoBtn v-if="!isGuest" prepend-icon="mdi-plus" class="mt-2" @click="createOpen = true">New team</AxoBtn>
        </AxoEmptyState>
      </template>
      <template #item.name="{ item }">
        <div class="d-flex align-center ga-3 py-2">
          <v-avatar size="34" class="axo-soft"><v-icon size="20" color="primary">mdi-account-group-outline</v-icon></v-avatar>
          <div>
            <div class="font-weight-medium">{{ item.name }}</div>
            <div v-if="item.description" class="text-caption axo-muted">{{ item.description }}</div>
          </div>
        </div>
      </template>
      <template #item.my_role="{ item }">
        <v-chip size="small" variant="tonal" :color="TEAM_ROLE_INFO[item.my_role as TeamRole]?.color">{{ teamRoleTitle(item.my_role) }}</v-chip>
      </template>
      <template #item.owns="{ item }">
        <span>{{ plural(item.ve_count ?? 0, 'VE') }} · {{ plural(item.bucket_count ?? 0, 'bucket') }}</span>
      </template>
      <template #item.created_at="{ item }">{{ formatDate(item.created_at) }}</template>
    </AxoDataTable>

    <TeamCreateDialog v-model="createOpen" @created="(t) => navigateTo(`/teams/${t.name}`)" />
  </div>
</template>

<script setup lang="ts">
import type { Team, TeamRole } from '../../utils/teams'

// The teams the person is in (their role, size, what each owns) and, on
// top, the invitations to join one.
useHead({ title: 'Teams · axo' })
const { apiFetch } = useApi()
const me = useMeStore()
const inbox = useInboxFollow()
const invitations = useInvitations()

const isGuest = computed(() => me.me?.platform_role === 'guest')
const teams = ref<Team[]>([])
const loading = ref(true)
const error = ref<unknown>(null)
async function load() {
  error.value = null
  try { teams.value = await apiFetch<Team[]>('/teams') } catch (err) { error.value = err } finally { loading.value = false }
}
onMounted(load)
watch(invitations.version, load)

const headers = [
  { title: 'Team', key: 'name', value: (t: Team) => `${t.name} ${t.description}` },
  { title: 'Your role', key: 'my_role' },
  { title: 'Members', key: 'member_count', align: 'end' as const },
  { title: 'Owns', key: 'owns', sortable: false },
  { title: 'Created', key: 'created_at' },
]
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

const createOpen = ref(false)
const busy = ref<string | null>(null)
async function answer(id: string, accept: boolean) {
  busy.value = id
  try { await invitations.answer(id, accept) } catch { /* said by the notifier */ } finally { busy.value = null }
}
</script>

<style scoped>
.axo-invite { display: flex; gap: 14px; align-items: center; padding: 14px 20px; flex-wrap: wrap; }
.axo-invite + .axo-invite { border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
</style>
