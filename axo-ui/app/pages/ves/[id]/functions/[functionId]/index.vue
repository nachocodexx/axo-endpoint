<template>
  <div>
    <template v-if="latest">
    <v-row density="compact" class="mb-2">
      <v-col v-for="c in cards" :key="c.title" cols="6" md="3">
        <AxoStatCard :label="c.title" :value="c.value" :icon="c.icon" :loading="figuresLoading" :help="c.help" />
      </v-col>
    </v-row>

    <v-row density="compact">
      <v-col cols="12" lg="7">
        <AxoPanel title="Parameters" icon="mdi-form-textbox" stacked class="mb-3" flush
                  help="What the run form asks for, read from the code: names, type hints, defaults and the docstring's Args.">
          <v-table v-if="params.length" density="compact" class="bg-transparent">
            <thead><tr><th>Name</th><th>Type</th><th class="d-none d-sm-table-cell">Default</th><th class="d-none d-md-table-cell">What it is</th></tr></thead>
            <tbody>
              <tr v-for="p in params" :key="p.name">
                <td class="axo-mono text-body-2">{{ p.name }}<span v-if="p.required" class="text-error" title="Required"> *</span></td>
                <td class="text-body-2">{{ TYPE_NAMES[p.type] ?? p.type }}</td>
                <td class="d-none d-sm-table-cell axo-mono text-caption">{{ p.required ? '—' : JSON.stringify(p.default) }}</td>
                <td class="d-none d-md-table-cell text-body-2">{{ p.description ?? '' }}</td>
              </tr>
            </tbody>
          </v-table>
          <div v-else class="text-body-2 axo-muted pa-4">It takes no parameters.</div>
        </AxoPanel>

        <AxoPanel title="Latest jobs" icon="mdi-briefcase-outline" stacked flush class="mb-3">
          <template #actions>
            <v-btn size="small" variant="text" class="text-none" :to="`/ves/${id}/functions/${functionId}/jobs`">All its jobs</v-btn>
          </template>
          <v-list v-if="recent.length" density="compact" class="bg-transparent py-0">
            <v-list-item v-for="j in recent" :key="j.job_id" :to="`/ves/${id}/jobs/${j.job_id}`">
              <template #prepend><div class="axo-job-chip"><AxoStatusChip kind="job" :value="j.status" size="x-small" /></div></template>
              <v-list-item-title class="axo-mono text-body-2">{{ j.job_id.slice(0, 16) }}</v-list-item-title>
              <v-list-item-subtitle>v{{ j.function_version }} · {{ startedByText(j) }} · {{ timeAgo(ts(j.created_at)) }}</v-list-item-subtitle>
              <template #append>
                <span class="text-caption">{{ j.duration_ms != null ? formatDuration(j.duration_ms) : '' }}</span>
              </template>
            </v-list-item>
          </v-list>
          <div v-else class="text-body-2 axo-muted pa-4">No jobs yet.</div>
        </AxoPanel>
      </v-col>

      <v-col cols="12" lg="5">
        <AxoPanel title="About it" icon="mdi-information-outline" stacked>
          <AxoKeyValue :items="facts" :columns="2" />
          <div v-if="latest.runtime_spec?.requirements?.length" class="mt-3">
            <div class="text-caption axo-muted mb-1">Packages</div>
            <div class="d-flex flex-wrap ga-1">
              <v-chip v-for="r in latest.runtime_spec.requirements" :key="r" size="small" variant="tonal" class="axo-mono">{{ r }}</v-chip>
            </div>
          </div>
        </AxoPanel>
      </v-col>
    </v-row>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { SummaryAnswer, TopAnswer } from 'axo-ui-kit/app/types/cp'
import type { Job, JobListRow } from '~/utils/jobs'

// A function at a glance: its last 24 hours, the parameters its run form
// asks for, its latest jobs and how it runs.
const { id, api, cp, vemUp } = useCurrentVe()
const { functionId, latest, versions } = useCurrentFunction()

const TYPE_NAMES: Record<string, string> = { number: 'Number', string: 'Text', boolean: 'Yes / no', json: 'JSON', data_ref: 'Bucket file' }
const params = computed(() => latest.value?.params_schema ?? [])

const facts = computed(() => {
  const v = latest.value!
  const s = v.runtime_spec ?? { type: 'process' }
  return [
    { label: 'Newest version', value: `v${v.version} of ${versions.value.length}` },
    { label: 'Runs as', value: runtimeLabel(s) },
    { label: 'Registered by', value: v.registered_by_name ?? '—' },
    { label: 'Registered', value: v.registered_at ? formatDateTime(ts(v.registered_at)) : '—' },
    { label: 'Attempts', value: (s.max_attempts ?? 1) > 1 ? `${s.max_attempts} (${s.retry_backoff ?? 'fixed'}, ${s.retry_delay_seconds ?? 1} s)` : '1 (no retries)',
      help: 'How many times a job is tried in all when its code fails. Platform failures are tried again on their own, free.' },
    { label: 'Results kept for', value: s.keep_result ? keepTitle(s.keep_result) : 'The VE’s default' },
    ...(s.type === 'container' ? [
      { label: 'Runners at most', value: String(s.max_concurrency ?? 1), help: 'Containers running it at the same time, in the whole VE.' },
      { label: 'Idle time', value: s.idle_ttl_seconds === -1 ? 'Always warm' : s.idle_ttl_seconds ? formatDuration(s.idle_ttl_seconds * 1000) : 'The endpoint’s default' },
    ] : []),
    { label: 'Code', value: v.code_format === 'source' ? 'Source' : 'Pickle (SDK)' },
    { label: 'Code hash', value: v.code_hash ? `${v.code_hash.slice(0, 12)}…` : '—', mono: true },
  ]
})

// ── last 24 hours + latest jobs ────────────────────────────────────────────
const figuresLoading = ref(true)
const day = ref<{ attempts: number; failed: number; avg: number | null } | null>(null)
const median = ref<number | null>(null)
const recent = ref<Job[]>([])
async function load() {
  if (!vemUp.value || !functionId.value) return
  const end = Date.now() / 1000
  const q = (o: Record<string, string | number>) => new URLSearchParams(Object.entries({ start: end - 86400, end, ...o })
    .map(([k, v]) => [k, String(v)])).toString()
  const [top, summary, jobs] = await Promise.all([
    cp<TopAnswer>(`/stats/top?${q({ by: 'function', limit: 200 })}`).catch(() => null),
    cp<SummaryAnswer>(`/stats/summary?${q({ metric: 'job.total_ms', statistics: 'median', dims: `function:${functionId.value},outcome:completed` })}`).catch(() => null),
    api<{ rows: JobListRow[] }>(`/jobs?function_id=${functionId.value}&limit=6`).catch(() => null),
  ])
  const row = top?.rows.find(r => r.function_id === functionId.value)
  day.value = { attempts: row?.attempts ?? 0, failed: row?.failed ?? 0, avg: row?.avg_execution_ms ?? null }
  median.value = summary?.groups[0]?.values.median ?? null
  recent.value = (jobs?.rows ?? []).flatMap(r => (r.type === 'job' ? [r.job] : r.jobs)).slice(0, 6)
  figuresLoading.value = false
}
watch([functionId, vemUp], load)
usePolling(load, 30_000)

const cards = computed(() => [
  { title: 'Jobs (24 h)', value: formatNumber(day.value?.attempts ?? 0), icon: 'mdi-briefcase-outline', help: 'Attempts that finished in the last 24 hours.' },
  { title: 'Failed', value: day.value?.attempts ? formatPercent((100 * (day.value.failed ?? 0)) / day.value.attempts) : '—', icon: 'mdi-alert-circle-outline' },
  { title: 'Median total time', value: formatMs(median.value), icon: 'mdi-timer-outline', help: 'Of the jobs that succeeded, waits included.' },
  { title: 'Average run time', value: formatMs(day.value?.avg ?? null), icon: 'mdi-run', help: 'The code’s own time, per attempt.' },
])
</script>

<style scoped>
.axo-job-chip { width: 104px; flex: 0 0 104px; }
</style>
