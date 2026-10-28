<template>
  <div>
    <AxoErrorAlert v-if="error" :error="error" title="The access list couldn't load" :retry="load" class="mb-4" />

    <AxoDataTable :headers="headers" :items="access" item-value="principal_id" :loading="loading" title="Who has access"
                  :searchable="access.length > 8"
                  help="People and teams who can use this bucket, from any VE they work in. Reader: list, preview, download. Writer: also upload and delete files. Owner: also settings and sharing.">
      <template #actions>
        <AxoBtn v-if="can('owner')" prepend-icon="mdi-account-plus-outline" @click="shareOpen = true">Share</AxoBtn>
      </template>
      <template #item.name="{ item }">
        <div class="d-flex align-center ga-2 py-2">
          <v-avatar size="30" :class="item.principal_type === 'GROUP' ? 'axo-soft' : 'axo-gradient'">
            <v-icon v-if="item.principal_type === 'GROUP'" size="18" color="primary">mdi-account-group-outline</v-icon>
            <span v-else class="text-caption font-weight-bold">{{ (item.name ?? '?').slice(0, 2).toUpperCase() }}</span>
          </v-avatar>
          <div>
            <div class="font-weight-medium">
              <NuxtLink v-if="item.principal_type === 'GROUP' && myTeams.has(teamName(item))" :to="`/teams/${teamName(item)}`"
                        class="axo-link">{{ teamLine(item) }}</NuxtLink>
              <template v-else>{{ teamLine(item) }}</template>
              <span v-if="item.principal_id === me.me?.user_id" class="text-caption axo-muted">(you)</span></div>
            <div class="text-caption axo-muted">{{ item.principal_type === 'GROUP' ? 'Team' : 'Person' }}</div>
          </div>
        </div>
      </template>
      <template #item.role="{ item }">
        <span v-if="item.role === 'owner' || !can('owner')">{{ bucketRoleTitle(item.role) }}</span>
        <v-select v-else :model-value="item.role" :items="assignable" density="compact" variant="outlined" hide-details
                  style="max-width: 150px" @update:model-value="setRole(item, $event)" />
      </template>
      <template #item.tools="{ item }">
        <v-btn v-if="item.role !== 'owner' && (can('owner') || item.principal_id === me.me?.user_id)" variant="text" size="small"
               color="error" class="text-none" @click="removeAccess(item)">
          {{ item.principal_id === me.me?.user_id ? 'Leave' : 'Remove' }}
        </v-btn>
      </template>
    </AxoDataTable>

    <AxoPanel v-if="invitations.length" title="Waiting for an answer" icon="mdi-email-fast-outline" class="mt-6" flush>
      <v-table class="bg-transparent">
        <tbody>
          <tr v-for="inv in invitations" :key="inv.id">
            <td><span class="font-weight-medium">{{ inv.invitee.name }}</span>
              <span class="text-caption axo-muted"> · {{ inv.invitee.type === 'email' ? 'by email' : inv.invitee.type }}</span></td>
            <td>{{ bucketRoleTitle(inv.role) }}</td>
            <td class="text-caption axo-muted">{{ inv.invited_by_name }}, {{ timeAgo(inv.created_at) }}</td>
            <td class="text-end"><v-btn variant="text" size="small" class="text-none" @click="withdraw(inv)">Cancel</v-btn></td>
          </tr>
        </tbody>
      </v-table>
    </AxoPanel>

    <v-dialog v-model="shareOpen" max-width="520">
      <v-card rounded="lg">
        <v-card-title class="pt-5 px-6">Share {{ name }}</v-card-title>
        <v-card-text class="px-6">
          <v-text-field v-model="who" label="Username, team name or email" autofocus
                        hint="They get an invitation and have access once they accept it. Someone without an account gets an email (as a Reader)."
                        persistent-hint class="mb-4" @keyup.enter="share" />
          <v-radio-group v-model="shareRole" hide-details>
            <v-radio v-for="r in assignable" :key="r.value" :value="r.value">
              <template #label><div><div class="font-weight-medium">{{ r.title }}</div>
                <div class="text-caption axo-muted">{{ BUCKET_ROLE_INFO[r.value].text }}</div></div></template>
            </v-radio>
          </v-radio-group>
        </v-card-text>
        <v-card-actions class="px-6 pb-5">
          <v-spacer />
          <AxoBtn look="plain" variant="text" @click="shareOpen = false">Close</AxoBtn>
          <AxoBtn :loading="sharing" :disabled="!who.trim()" @click="share">Send invitation</AxoBtn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import type { BucketRole } from '../../../../utils/buckets'

// Who can use the bucket (everyone with access sees it); the Owner shares
// it (an invitation to accept), changes Reader <-> Writer in place, and
// removes access. Anyone may leave.
const { name, can, call } = useCurrentBucket()
const me = useMeStore()
const notify = useNotifier()
const confirmDialog = useConfirm()

interface Line { principal_id: string; principal_type: string; name: string | null; role: BucketRole }
const access = ref<Line[]>([])
const invitations = ref<any[]>([])
const loading = ref(true)
const error = ref<unknown>(null)
const headers = [
  { title: 'Who', key: 'name' }, { title: 'Role', key: 'role' },
  { title: '', key: 'tools', sortable: false, align: 'end' as const },
]
const assignable = (['reader', 'writer'] as const).map(r => ({ title: BUCKET_ROLE_INFO[r].title, value: r }))

/** "ml-team (members)" -> "ml-team members". */
const myTeams = computed(() => new Set((me.me?.teams ?? []).map(t => t.name)))
const teamName = (l: Line) => (l.name ?? '').replace(/ \((owner|admins|members)\)$/, '')
const teamLine = (l: Line) => (l.name ?? l.principal_id).replace(/ \((owner|admins|members)\)$/, ' $1')

async function load() {
  error.value = null
  try {
    const answer = await call<{ access: Line[]; invitations: any[] }>('/access')
    const order = (r: string) => -BUCKET_ROLES.indexOf(r as BucketRole)
    access.value = answer.access.sort((a, b) => order(a.role) - order(b.role) || String(a.name).localeCompare(String(b.name)))
    invitations.value = answer.invitations
  } catch (err) { error.value = err } finally { loading.value = false }
}
watch(name, load)
onMounted(load)

async function setRole(item: Line, role: BucketRole) {
  const before = item.role
  item.role = role
  try {
    await call(`/access/${item.principal_id}`, { method: 'PUT', body: { role, principal_type: item.principal_type } })
    notify.success(`${teamLine(item)} is now ${bucketRoleTitle(role)}`)
  } catch (err) {
    item.role = before
    notify.error(err, "The role couldn't be changed")
  }
}

async function removeAccess(item: Line) {
  const leaving = item.principal_id === me.me?.user_id
  if (!await confirmDialog.confirm({
    title: leaving ? `Leave ${name.value}?` : `Remove ${teamLine(item)}?`, danger: true, confirmText: leaving ? 'Leave' : 'Remove',
    text: leaving ? "You won't see this bucket any more until someone shares it with you again."
      : 'They lose access at once, and so do jobs that run for them.',
  })) return
  try {
    await call(`/access/${item.principal_id}`, { method: 'DELETE', query: { principal_type: item.principal_type } })
    access.value = access.value.filter(a => a !== item)
    notify.success(leaving ? `You left ${name.value}` : 'Access removed')
    if (leaving) await navigateTo('/buckets')
  } catch (err) { notify.error(err, "The access couldn't be removed") }
}

const shareOpen = ref(false)
const who = ref('')
const shareRole = ref<BucketRole>('reader')
const sharing = ref(false)
async function share() {
  if (!who.value.trim()) return
  sharing.value = true
  try {
    const inv = await call('/invitations', { method: 'POST', body: { who: who.value.trim(), role: shareRole.value } })
    invitations.value.push(inv)
    notify.success(`Invitation sent to ${inv.invitee?.name ?? who.value}`)
    shareOpen.value = false
    who.value = ''
  } catch (err) {
    notify.error(err, "The invitation couldn't be sent")
  } finally {
    sharing.value = false
  }
}

async function withdraw(inv: any) {
  try {
    await useApi().apiFetch(`/invitations/${inv.id}`, { method: 'DELETE' })
    invitations.value = invitations.value.filter(i => i.id !== inv.id)
    notify.success('Invitation cancelled')
  } catch (err) { notify.error(err, "The invitation couldn't be cancelled") }
}

</script>
