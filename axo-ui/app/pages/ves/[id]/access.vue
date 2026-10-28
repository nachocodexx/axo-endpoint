<template>
  <div>
    <AxoErrorAlert v-if="error" :error="error" title="The access list couldn't load" :retry="load" class="mb-4" />

    <AxoDataTable :headers="headers" :items="access" item-value="principal_id" :loading="loading" title="Who has access"
                  :searchable="access.length > 8" help="People and teams with a role in this VE. A team's role applies to its members by their team role.">
      <template #actions>
        <AxoBtn prepend-icon="mdi-account-plus-outline" @click="inviteOpen = true">Invite</AxoBtn>
      </template>
      <template #item.name="{ item }">
        <div class="d-flex align-center ga-2 py-2">
          <v-avatar size="30" :class="item.principal_type === 'GROUP' ? 'axo-soft' : 'axo-gradient'">
            <v-icon v-if="item.principal_type === 'GROUP'" size="18" color="primary">mdi-account-group-outline</v-icon>
            <span v-else class="text-caption font-weight-bold">{{ (item.name ?? '?').slice(0, 2).toUpperCase() }}</span>
          </v-avatar>
          <div>
            <div class="font-weight-medium">
              <NuxtLink v-if="item.principal_type === 'GROUP' && myTeams.has(item.name)" :to="`/teams/${item.name}`" class="axo-link">{{ item.name }}</NuxtLink>
              <template v-else>{{ item.name ?? item.principal_id }}</template>
              <span v-if="item.principal_id === me.me?.user_id" class="text-caption axo-muted">(you)</span></div>
            <div class="text-caption axo-muted d-flex align-center ga-1">
              {{ item.principal_type === 'GROUP' ? 'Team' : 'Person' }}
              <AxoHelp v-if="item.principal_type === 'GROUP'" title="A team's role"
                       :text="`Everyone in ${item.name} gets this role here. Changing who is in the team is done on the team's page.`" />
            </div>
          </div>
        </div>
      </template>
      <template #item.role="{ item }">
        <span v-if="item.role === 'owner' || !mayChange(item)">{{ roleTitle(item.role) }}</span>
        <v-select v-else :model-value="item.role" :items="assignable" density="compact" variant="outlined" hide-details
                  style="max-width: 170px" @update:model-value="setRole(item, $event)" />
      </template>
      <template #item.tools="{ item }">
        <v-btn v-if="item.role !== 'owner' && (mayChange(item) || item.principal_id === me.me?.user_id)" variant="text" size="small"
               color="error" @click="setRole(item, null)">
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
            <td>{{ roleTitle(inv.role) }}</td>
            <td class="text-caption axo-muted">{{ inv.invited_by_name }}, {{ timeAgo(inv.created_at) }}</td>
            <td class="text-end"><v-btn variant="text" size="small" @click="withdraw(inv)">Withdraw</v-btn></td>
          </tr>
        </tbody>
      </v-table>
    </AxoPanel>

    <v-dialog v-model="inviteOpen" max-width="520">
      <v-card rounded="lg">
        <v-card-title class="pt-5 px-6">Invite to {{ ve?.name }}</v-card-title>
        <v-card-text class="px-6">
          <v-text-field v-model="who" label="Username, team name or email" autofocus
                        hint="Someone without an account gets an email to sign up (as a Viewer)." persistent-hint class="mb-4" />
          <v-radio-group v-model="inviteRole" hide-details>
            <v-radio v-for="r in assignable" :key="r.value" :value="r.value">
              <template #label><div><div class="font-weight-medium">{{ r.title }}</div>
                <div class="text-caption axo-muted">{{ ROLE_INFO[r.value].text }}</div></div></template>
            </v-radio>
          </v-radio-group>
        </v-card-text>
        <v-card-actions class="px-6 pb-5">
          <v-spacer />
          <AxoBtn look="plain" variant="text" @click="inviteOpen = false">Close</AxoBtn>
          <AxoBtn :loading="inviting" :disabled="!who.trim()" @click="invite">Send invitation</AxoBtn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
// Who has a role in the VE (Admin+), invitations (they must accept), role
// changes and removals. Only the Owner gives or changes the Admin role;
// anyone may leave.
const { id, ve, role, cp } = useCurrentVe()
const me = useMeStore()
const myTeams = computed(() => new Set((me.me?.teams ?? []).map(t => t.name)))
const notify = useNotifier()
const confirmDialog = useConfirm()

const access = ref<any[]>([])
const invitations = ref<any[]>([])
const loading = ref(true)
const error = ref<unknown>(null)
const headers = [
  { title: 'Who', key: 'name' }, { title: 'Role', key: 'role' },
  { title: '', key: 'tools', sortable: false, align: 'end' as const },
]

async function load() {
  error.value = null
  try {
    const answer = await cp<{ access: any[]; invitations: any[] }>('/access')
    const order = (r: string) => -VE_ROLES.indexOf(r as VeRole)
    access.value = answer.access.sort((a, b) => order(a.role) - order(b.role) || String(a.name).localeCompare(String(b.name)))
    invitations.value = answer.invitations
  } catch (err) { error.value = err } finally { loading.value = false }
}
watch(id, load)
onMounted(load)

const assignable = computed(() => (['viewer', 'developer', 'admin'] as VeRole[])
  .filter(r => r !== 'admin' || role.value === 'owner')
  .map(r => ({ title: ROLE_INFO[r].title, value: r })))
const mayChange = (item: any) => role.value === 'owner' || (item.role !== 'admin' && roleAtLeast(role.value, 'admin'))

async function setRole(item: any, newRole: string | null) {
  const leaving = item.principal_id === me.me?.user_id
  if (newRole === null && !await confirmDialog.confirm({
    title: leaving ? `Leave ${ve.value?.name}?` : `Remove ${item.name ?? 'them'}?`, danger: true,
    confirmText: leaving ? 'Leave' : 'Remove',
    text: leaving ? "You won't see this VE any more until someone invites you again." : 'They lose every right in this VE at once.',
  })) return
  const before = item.role
  item.role = newRole ?? item.role
  try {
    await cp(`/access/${item.principal_id}`, { method: 'PUT', body: { principal_type: item.principal_type, role: newRole } })
    if (newRole === null) access.value = access.value.filter(a => a !== item)
    notify.success(newRole === null ? (leaving ? `You left ${ve.value?.name}` : 'Access removed') : `${item.name} is now ${roleTitle(newRole)}`)
    if (leaving) { useVesStore().remove(id.value); await navigateTo('/ves') }
  } catch (err) {
    item.role = before
    notify.error(err, "The role couldn't be changed")
  }
}

const inviteOpen = ref(false)
const who = ref('')
const inviteRole = ref<VeRole>('developer')
const inviting = ref(false)
async function invite() {
  inviting.value = true
  try {
    const inv = await cp('/invitations', { method: 'POST', body: { who: who.value.trim(), role: inviteRole.value } })
    invitations.value.push(inv)
    notify.success(`Invitation sent to ${inv.invitee?.name ?? who.value}`)
    inviteOpen.value = false
    who.value = ''
  } catch (err) {
    notify.error(err, "The invitation couldn't be sent")
  } finally {
    inviting.value = false
  }
}

async function withdraw(inv: any) {
  try {
    await useApi().apiFetch(`/invitations/${inv.id}`, { method: 'DELETE' })
    invitations.value = invitations.value.filter(i => i.id !== inv.id)
    notify.success('Invitation withdrawn')
  } catch (err) { notify.error(err, "The invitation couldn't be withdrawn") }
}
</script>
