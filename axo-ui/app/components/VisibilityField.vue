<template>
  <div>
    <div class="d-flex align-center ga-1 mb-1">
      <span class="text-subtitle-2">Who sees runs and results</span>
      <AxoHelp title="Who sees runs and results" text="The VE's Admins and Owner see everything. Anyone else sees what they started, plus what this setting shares. It applies at once (it isn't part of a version)." />
    </div>
    <AxoSkeleton v-if="loading" :lines="2" />
    <template v-else-if="current">
      <v-radio-group v-model="mode" hide-details density="compact" :disabled="!current.can_change || saving">
        <v-radio value="runner" label="Only who started each one (and the VE's Admins)" />
        <v-radio value="ve" label="Everyone in the VE" />
        <v-radio value="teams" label="These teams" />
      </v-radio-group>
      <v-expand-transition>
        <v-autocomplete v-if="mode === 'teams'" v-model="teams" :items="teamItems" multiple chips closable-chips density="compact" variant="outlined"
                        label="Teams" :disabled="!current.can_change || saving" class="mt-2" hide-details="auto"
                        :no-data-text="'You are in no team'" />
      </v-expand-transition>
      <div class="d-flex align-center ga-2 mt-2">
        <span v-if="!current.can_change" class="text-caption axo-muted">Only who made it, or the VE's Admins, change this.</span>
        <span v-else-if="current.set_at" class="text-caption axo-muted">Set {{ timeAgo(ts(current.set_at)) }}</span>
        <v-spacer />
        <v-btn v-if="current.can_change && changed" size="small" color="primary" variant="flat" :loading="saving"
               :disabled="mode === 'teams' && !teams.length" @click="save">Apply now</v-btn>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
// The "who sees runs and results" setting of a function, choreography or
// object (GET / PUT /visibility/{kind}/{ref}); applies at once.
import type { Visibility } from '~/utils/choreographies'
import type { Team } from '~/utils/teams'

const props = defineProps<{ kind: 'function' | 'choreography' | 'object'; refId: string; name?: string }>()
const { api } = useCurrentVe()
const { apiFetch } = useApi()
const notifier = useNotifier()
const current = ref<Visibility | null>(null)
const loading = ref(true)
const saving = ref(false)
const mode = ref<Visibility['mode']>('runner')
const teams = ref<string[]>([])
const myTeams = ref<Team[]>([])
const teamItems = computed(() => {
  const known = new Map(myTeams.value.map(t => [t.team_id, t.name]))
  return [...new Set([...myTeams.value.map(t => t.team_id), ...teams.value])].map(id => ({ value: id, title: known.get(id) ?? id }))
})
const changed = computed(() => !!current.value && (mode.value !== current.value.mode
  || (mode.value === 'teams' && JSON.stringify([...teams.value].sort()) !== JSON.stringify([...current.value.teams].sort()))))
async function load() {
  loading.value = true
  try {
    const [v, t] = await Promise.all([api<Visibility>(`/visibility/${props.kind}/${encodeURIComponent(props.refId)}`), apiFetch<Team[]>('/teams').catch(() => [])])
    current.value = v; myTeams.value = t; mode.value = v.mode; teams.value = [...v.teams]
  } catch { current.value = null } finally { loading.value = false }
}
async function save() {
  saving.value = true
  try {
    current.value = await api<Visibility>(`/visibility/${props.kind}/${encodeURIComponent(props.refId)}`, { method: 'PUT',
      body: { mode: mode.value, teams: mode.value === 'teams' ? teams.value : [], name: props.name } })
    notifier.success('Applied: it counts from now on')
  } catch (err) { notifier.error(err, "It couldn't be changed") } finally { saving.value = false }
}
watch(() => props.refId, load, { immediate: true })
</script>
