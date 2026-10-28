<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-2 mb-3">
      <v-select v-model="status" :items="RUN_STATUS_CHOICES" density="compact" variant="outlined" hide-details clearable placeholder="Any status" class="axo-filter" />
      <v-select v-model="by" :items="BY" density="compact" variant="outlined" hide-details class="axo-filter" />
      <v-select v-model="since" :items="SINCE" density="compact" variant="outlined" hide-details class="axo-filter" />
      <v-spacer />
      <span class="text-caption axo-muted">{{ rows.length }} run{{ rows.length === 1 ? '' : 's' }}</span>
    </div>
    <ChoreographyRunsTable :rows="rows" :base="base" :loading="loading" :empty-title="status || since || by ? 'No run matches' : 'No runs yet'"
                           :empty-text="status || since || by ? 'Change the filters.' : 'Start one with Run….'" />
    <div v-if="more" class="d-flex justify-center mt-3">
      <v-btn variant="text" prepend-icon="mdi-chevron-down" :loading="loading" @click="limit += 50">Load more</v-btn>
    </div>
  </div>
</template>

<script setup lang="ts">
// Every run of this choreography you may see (all of them when it shares
// its runs with you, or you're a VE Admin / Owner), newest first.
import type { RunSummary } from '~/utils/choreographies'

const { id, api, vemUp } = useCurrentVe()
const { name } = useCurrentChoreography()
const route = useRoute()
const router = useRouter()
const base = computed(() => `/ves/${id.value}/choreographies/${encodeURIComponent(name.value)}`)
const SINCE = [{ value: null, title: 'Any time' }, { value: 60, title: 'Last hour' }, { value: 1440, title: 'Last 24 hours' }, { value: 10080, title: 'Last 7 days' }]
const BY = [{ value: null, title: 'Started by anyone' }, { value: 'user', title: 'By people' }, { value: 'api_key', title: 'By API keys' },
  { value: 'trigger', title: 'By triggers' }, { value: 'choreography', title: 'By other choreographies' }]
const by = ref<string | null>((route.query.by as string) ?? null)
const status = ref<string | null>((route.query.status as string) ?? null)
const since = ref<number | null>(route.query.since ? Number(route.query.since) : null)
const limit = ref(50)
const rows = ref<RunSummary[]>([])
const loading = ref(false)
const more = computed(() => rows.value.length >= limit.value)
watch([status, since, by], () => { limit.value = 50; router.replace({ query: { ...route.query, status: status.value ?? undefined, since: since.value ?? undefined, by: by.value ?? undefined } }) })
async function load() {
  if (!vemUp.value) return
  loading.value = true
  try {
    rows.value = await api<RunSummary[]>(`/choreographies/${encodeURIComponent(name.value)}/runs`,
      { query: { limit: limit.value, status: status.value ?? undefined, since_minutes: since.value ?? undefined, started_by_type: by.value ?? undefined } })
  } catch { /* the frame shows errors */ } finally { loading.value = false }
}
watch([name, vemUp, status, since, by, limit], load, { immediate: true })
usePolling(load, 15_000)
let t: ReturnType<typeof setTimeout> | undefined
useVeLive(() => (vemUp.value ? 'choreographies' : null), () => { clearTimeout(t); t = setTimeout(load, 500) })
</script>
