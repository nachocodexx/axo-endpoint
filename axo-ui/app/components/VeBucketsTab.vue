<template>
  <div>
    <v-alert type="info" variant="tonal" density="compact" class="mb-3" icon="mdi-shield-lock-outline">
      Managed by the platform: each VE keeps its functions' code, its jobs' results and its objects' state here. You can't change them;
      results are removed when their keep time ends.
    </v-alert>
    <AxoErrorAlert v-if="error" :error="error" title="The VE buckets couldn't load" :retry="load" class="mb-4" />
    <AxoDataTable :headers="headers" :items="rows" item-value="bucket" :loading="loading" title="VE buckets"
                  :clickable="(r: VeBucket) => r.kind === 'results'" @row="(r: VeBucket) => openResults(r)">
      <template #empty>
        <AxoEmptyState icon="mdi-cube-outline" title="No VEs" text="The buckets of the VEs you work in show here." />
      </template>
      <template #item.kind="{ item }">
        <div class="d-flex align-center ga-2 py-2">
          <v-icon :icon="kindOf(item).icon" :color="kindOf(item).color" size="20" />
          <div>
            <div class="font-weight-medium">{{ kindOf(item).title }}</div>
            <div class="text-caption axo-muted axo-mono">{{ item.bucket }}</div>
          </div>
        </div>
      </template>
      <template #item.ve_name="{ item }">
        <NuxtLink :to="`/ves/${item.ve_id}`" class="axo-link" @click.stop>{{ item.ve_name ?? item.ve_id }}</NuxtLink>
      </template>
      <template #item.bytes="{ item }">
        <span v-if="item.bytes != null">{{ formatBytes(item.bytes) }}</span>
        <span v-else class="text-caption axo-muted">{{ item.own_results_only ? 'your results only' : '—' }}</span>
      </template>
      <template #item.files="{ item }">{{ item.files == null ? '—' : formatNumber(item.files, false) }}</template>
      <template #item.last_change="{ item }">
        <span v-if="item.last_change" :title="formatDateTime(item.last_change)">{{ timeAgo(item.last_change) }}</span>
        <span v-else class="axo-muted">—</span>
      </template>
      <template #item.tools="{ item }">
        <v-btn v-if="item.kind === 'results'" size="small" variant="text" class="text-none" prepend-icon="mdi-folder-open-outline"
               @click.stop="openResults(item)">{{ item.own_results_only ? 'My results' : 'Browse' }}</v-btn>
      </template>
    </AxoDataTable>

    <!-- one VE's results, as this person may see them -->
    <v-dialog :model-value="!!open" max-width="820" scrollable @update:model-value="!$event && (open = null)">
      <v-card v-if="open" :title="`Results · ${open.ve_name ?? open.ve_id}`"
              :subtitle="open.own_results_only ? 'Your jobs\' results, and the ones shared with you' : 'Every job\'s result'">
        <v-card-text>
          <v-alert v-if="!running(open)" type="info" variant="tonal" density="compact">
            The VE is {{ lifecycle(open).toLowerCase() || 'not running' }}: start it to browse its results.
          </v-alert>
          <template v-else>
            <AxoErrorAlert v-if="resultsError" :error="resultsError" title="The results couldn't load" :retry="() => loadResults(false)" class="mb-3" />
            <div v-if="results" class="text-body-2 axo-muted mb-2">{{ formatNumber(results.files, false) }} files · {{ formatBytes(results.bytes) }}</div>
            <AxoSkeleton v-if="resultsLoading && !results" :lines="5" />
            <v-table v-else-if="results?.results.length" density="compact">
              <thead><tr><th>Job</th><th>What ran</th><th class="d-none d-sm-table-cell">Saved</th><th class="text-end">Size</th><th /></tr></thead>
              <tbody>
                <tr v-for="r in results.results" :key="r.job_id">
                  <td><NuxtLink :to="`/ves/${open.ve_id}/jobs/${r.job_id}`" class="axo-link axo-mono text-body-2">{{ r.job_id.slice(0, 12) }}</NuxtLink></td>
                  <td class="text-body-2">{{ whatRan(r) }}</td>
                  <td class="d-none d-sm-table-cell text-body-2 text-no-wrap">{{ r.saved_at ? timeAgo(ts(r.saved_at)) : '—' }}
                    <div v-if="r.keep_until" class="text-caption axo-muted">kept until {{ formatDate(ts(r.keep_until)) }}</div></td>
                  <td class="text-end text-body-2">{{ formatBytes(r.size) }}</td>
                  <td class="text-end"><v-btn icon="mdi-download" size="small" variant="text" :aria-label="`Download the result of ${r.job_id}`"
                                              :loading="downloading === r.job_id" @click="download(r.job_id)" /></td>
                </tr>
              </tbody>
            </v-table>
            <AxoEmptyState v-else-if="results" icon="mdi-file-outline" title="No results kept" text="Results of finished jobs show here until their keep time ends." />
            <div v-if="results?.next" class="text-center mt-2">
              <v-btn variant="tonal" class="text-none" :loading="resultsLoading" @click="loadResults(true)">Load more</v-btn>
            </div>
          </template>
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn variant="text" @click="open = null">Close</v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
// The Buckets page's "VE buckets" tab: every VE's three platform buckets
// (read-only; sizes only for the VE's Owner / Admin), and its results as
// this person may browse them (the VEM's /results, through the front door).
interface VeBucket {
  ve_id: string; ve_name?: string | null; my_role: string; kind: 'functions' | 'results' | 'objects'; bucket: string
  bytes: number | null; files: number | null; last_change: number | null; own_results_only: boolean
}
interface ResultRow {
  job_id: string; name?: string; size?: number; saved_at?: string; keep_until?: string | null; kind?: string
  function_name?: string | null; function_version?: number | null; object_name?: string | null; object_method?: string | null
  choreography?: { choreography_name?: string; step?: string } | null
}
interface ResultsPage { results: ResultRow[]; files: number; bytes: number; next: { before: string; before_id: string } | null }

const KINDS = {
  functions: { title: 'Functions code', icon: 'mdi-function-variant', color: 'primary' },
  results: { title: 'Job results', icon: 'mdi-file-check-outline', color: 'success' },
  objects: { title: 'Object state', icon: 'mdi-shape-outline', color: 'brand-3' },
} as const
const kindOf = (r: VeBucket) => KINDS[r.kind]
const { apiFetch } = useApi()
const ves = useVesStore()
const notifier = useNotifier()
const rows = ref<VeBucket[]>([])
const loading = ref(true)
const error = ref<unknown>(null)
async function load() {
  loading.value = true
  try { rows.value = await apiFetch<VeBucket[]>('/me/ve-buckets'); error.value = null } catch (err) { error.value = err } finally { loading.value = false }
}
onMounted(() => { load(); ves.load() })
const headers = [
  { title: 'Bucket', key: 'kind', value: (r: VeBucket) => `${r.bucket} ${KINDS[r.kind].title}` },
  { title: 'VE', key: 've_name' },
  { title: 'Used', key: 'bytes' },
  { title: 'Files', key: 'files', align: 'end' as const },
  { title: 'Last change', key: 'last_change', value: (r: VeBucket) => r.last_change ?? 0 },
  { title: '', key: 'tools', sortable: false, align: 'end' as const },
]

const lifecycle = (r: VeBucket) => ves.byId(r.ve_id)?.lifecycle ?? ''
const running = (r: VeBucket) => ['Running', 'Scaling'].includes(lifecycle(r))
const open = ref<VeBucket | null>(null)
const results = ref<ResultsPage | null>(null)
const resultsLoading = ref(false)
const resultsError = ref<unknown>(null)
function openResults(r: VeBucket) {
  if (r.kind !== 'results') return
  open.value = r
  results.value = null
  if (running(r)) loadResults(false)
}
async function loadResults(more: boolean) {
  const r = open.value
  if (!r) return
  resultsLoading.value = true
  try {
    const page = await apiFetch<ResultsPage>(`/ves/${r.ve_id}/api/results`, { query: more && results.value?.next ? results.value.next : {} })
    results.value = more && results.value ? { ...page, results: [...results.value.results, ...page.results] } : page
    resultsError.value = null
  } catch (err) { resultsError.value = err } finally { resultsLoading.value = false }
}
function whatRan(r: ResultRow) {
  if (r.choreography?.choreography_name) return `${r.choreography.choreography_name} · ${r.choreography.step ?? 'step'}`
  if (r.object_name) return `${r.object_name} · ${r.object_method ?? 'call'}`
  return r.function_name ? `${r.function_name}${r.function_version ? ` v${r.function_version}` : ''}` : (r.name ?? '—')
}
const downloading = ref<string | null>(null)
async function download(jobId: string) {
  const r = open.value
  if (!r) return
  downloading.value = jobId
  try {
    const value = await apiFetch(`/ves/${r.ve_id}/api/jobs/${jobId}/result`)
    saveBlob(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }), `${jobId}.json`)
  } catch (err) { notifier.error(err, "The result couldn't be downloaded") } finally { downloading.value = null }
}
</script>
