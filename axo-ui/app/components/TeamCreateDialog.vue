<template>
  <v-dialog :model-value="modelValue" max-width="520" @update:model-value="emit('update:modelValue', $event)">
    <v-card rounded="lg">
      <v-card-title class="pt-5 px-6">New team</v-card-title>
      <v-card-text class="px-6">
        <v-text-field v-model="name" label="Name" autofocus :error-messages="nameError ? [nameError] : []"
                      :loading="checking" hint="Lowercase letters, digits and '-'. Teams and people share the same names."
                      persistent-hint class="mb-3" @keyup.enter="create">
          <template v-if="check?.free && check.name === name.trim().toLowerCase()" #append-inner>
            <v-icon color="success" size="18">mdi-check-circle</v-icon>
          </template>
        </v-text-field>
        <v-textarea v-model="description" label="Description (optional)" rows="2" auto-grow
                    hint="What the team is for, e.g. &quot;Sales analytics and reports&quot;" persistent-hint />
        <div class="text-caption axo-muted mt-4">
          You'll be its Owner. Then invite people from its Members tab, and rent VEs or create buckets owned by the team.
        </div>
      </v-card-text>
      <v-card-actions class="px-6 pb-5">
        <v-spacer />
        <AxoBtn look="plain" variant="text" @click="emit('update:modelValue', false)">Cancel</AxoBtn>
        <AxoBtn :loading="busy" :disabled="!check?.free || check.name !== name.trim().toLowerCase()" @click="create">Create team</AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { Team } from '../utils/teams'

// A name (checked as it's typed: it may already be a person's or a team's)
// and a description; the creator becomes the Owner.
const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; created: [Team] }>()
const { apiFetch } = useApi()
const notify = useNotifier()
const me = useMeStore()

const name = ref('')
const description = ref('')
const busy = ref(false)
const checking = ref(false)
const check = ref<{ name: string; free: boolean; reason: string | null } | null>(null)
const nameError = computed(() => (name.value.trim() && check.value && !check.value.free ? check.value.reason : null))

watch(() => props.modelValue, (open) => { if (open) { name.value = ''; description.value = ''; check.value = null } })

let timer: ReturnType<typeof setTimeout> | undefined
watch(name, (value) => {
  clearTimeout(timer)
  check.value = null
  if (!value.trim()) return
  checking.value = true
  timer = setTimeout(async () => {
    try {
      const answer = await apiFetch<{ name: string; free: boolean; reason: string | null }>('/names/check', { query: { name: value } })
      if (value === name.value) check.value = answer
    } catch { /* checked again on create */ } finally { checking.value = false }
  }, 300)
})

async function create() {
  if (!check.value?.free || busy.value) return
  busy.value = true
  try {
    const team = await apiFetch<Team>('/teams', { method: 'POST', body: { name: name.value.trim().toLowerCase(), description: description.value.trim() } })
    notify.success(`Team ${team.name} created: you're its Owner`)
    await me.load(true)
    emit('created', team)
    emit('update:modelValue', false)
  } catch (err) { notify.error(err, "The team couldn't be created") } finally { busy.value = false }
}
</script>
