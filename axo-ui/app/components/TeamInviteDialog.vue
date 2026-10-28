<template>
  <v-dialog :model-value="modelValue" max-width="520" @update:model-value="emit('update:modelValue', $event)">
    <v-card rounded="lg">
      <v-card-title class="pt-5 px-6">Invite to {{ team.name }}</v-card-title>
      <v-card-text class="px-6">
        <v-text-field v-model="who" label="Username or email" autofocus @keyup.enter="invite"
                      hint="They join when they accept. Someone without an account gets an email to sign up." persistent-hint class="mb-4" />
        <v-radio-group v-model="role" hide-details>
          <v-radio v-for="r in choices" :key="r" :value="r" :disabled="r === 'admin' && byEmail">
            <template #label>
              <div>
                <div class="font-weight-medium">{{ TEAM_ROLE_INFO[r].title }}</div>
                <div class="text-caption axo-muted">{{ TEAM_ROLE_INFO[r].text }}</div>
              </div>
            </template>
          </v-radio>
        </v-radio-group>
        <div v-if="byEmail" class="text-caption axo-muted mt-2">
          Someone invited by email joins with a new account; they can be made Admin once they have their own.
        </div>
        <div v-if="myRole !== 'owner'" class="text-caption axo-muted mt-2">Only the Team Owner invites Admins.</div>
      </v-card-text>
      <v-card-actions class="px-6 pb-5">
        <v-spacer />
        <AxoBtn look="plain" variant="text" @click="emit('update:modelValue', false)">Close</AxoBtn>
        <AxoBtn :loading="busy" :disabled="!who.trim()" @click="invite">Send invitation</AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { Invitation, Team, TeamRole } from '../utils/teams'

// Invite a person (username or email) as Member, or as Admin (Owner only;
// never by email: those accounts are guests, who can't be admins).
const props = defineProps<{ modelValue: boolean; team: Team; myRole: TeamRole }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; invited: [Invitation] }>()
const { apiFetch } = useApi()
const notify = useNotifier()

const who = ref('')
const role = ref<'member' | 'admin'>('member')
const busy = ref(false)
const byEmail = computed(() => who.value.includes('@'))
const choices = computed(() => (props.myRole === 'owner' ? ['member', 'admin'] : ['member']) as Array<'member' | 'admin'>)
watch(byEmail, (e) => { if (e) role.value = 'member' })
watch(() => props.modelValue, (open) => { if (open) { who.value = ''; role.value = 'member' } })

async function invite() {
  if (!who.value.trim() || busy.value) return
  busy.value = true
  try {
    const inv = await apiFetch<Invitation>(`/teams/${props.team.team_id}/invitations`, {
      method: 'POST', body: { who: who.value.trim(), role: role.value },
    })
    notify.success(`Invitation sent to ${inv.invitee?.name ?? who.value}`)
    emit('invited', inv)
    emit('update:modelValue', false)
  } catch (err) { notify.error(err, "The invitation couldn't be sent") } finally { busy.value = false }
}
</script>
