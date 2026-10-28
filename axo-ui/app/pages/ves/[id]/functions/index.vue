<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <div class="text-h6 d-flex align-center ga-1">Functions
          <AxoHelp title="Functions" text="Python functions registered in this VE (from the SDK or from here). Each change of the code is a new version; jobs run one version. Simple ones run on the endpoints themselves; Container ones in their own Python with their own packages." />
        </div>
        <div class="text-body-2 axo-muted">{{ shown.length }} function{{ shown.length === 1 ? '' : 's' }} · jobs of the last 24 hours</div>
      </div>
      <v-spacer />
      <AxoBtn v-if="can('developer')" prepend-icon="mdi-plus" :disabled="!vemUp" :to="`/ves/${id}/functions/new`">New function</AxoBtn>
    </div>

    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: its functions show here once it runs (a sleeping VE wakes with its next job).
    </v-alert>
    <AxoErrorAlert v-if="functions.error.value" :error="functions.error.value" title="The functions couldn't load"
                   :retry="() => functions.load(true)" class="mb-4" />

    <template v-if="vemUp">
      <div class="d-flex align-center flex-wrap ga-2 mb-3">
        <v-text-field v-model="search" density="compact" variant="outlined" hide-details clearable prepend-inner-icon="mdi-magnify"
                      placeholder="Name" class="axo-filter" />
        <v-select v-model="runtime" :items="RUNTIMES" density="compact" variant="outlined" hide-details clearable
                  placeholder="Any way of running" class="axo-filter" />
        <v-select v-model="state" :items="STATES" density="compact" variant="outlined" hide-details clearable
                  placeholder="Any state" class="axo-filter" />
        <v-switch v-model="showDeleted" color="primary" inset hide-details density="compact" label="Show deleted" class="flex-grow-0" />
      </div>

      <AxoDataTable :headers="headers" :items="shown" item-value="function_id" :loading="functions.loading.value && !functions.rows.value.length"
                    :searchable="false" :row-to="(f) => `/ves/${id}/functions/${f.function_id}`"
                    :sort-by="[{ key: 'name', order: 'asc' }]" empty-icon="mdi-function-variant"
                    :empty-title="functions.rows.value.length ? 'No function matches' : 'No functions yet'"
                    :empty-text="functions.rows.value.length ? 'Change the filters.' : can('developer') ? 'Register one here or with the SDK.' : 'Developers register them.'">
        <template #item.name="{ item }">
          <div class="py-2 min-w-0">
            <div class="text-body-2 font-weight-medium text-truncate">{{ item.name }}</div>
            <div v-if="item.latest.description" class="text-caption axo-muted text-truncate" style="max-width: 320px">{{ item.latest.description }}</div>
          </div>
        </template>
        <template #item.version="{ item }">
          <span class="text-no-wrap">v{{ item.latest.version }}</span><span class="text-caption axo-muted text-no-wrap"> · {{ item.versions.length }} version{{ item.versions.length === 1 ? '' : 's' }}</span>
        </template>
        <template #item.runtime="{ item }"><span class="text-body-2 text-no-wrap">{{ runtimeLabel(item.latest.runtime_spec) }}</span></template>
        <template #item.state="{ item }"><AxoStatusChip kind="function" :value="functionState(item.latest, item.deleted)" /></template>
        <template #item.jobs="{ item }">
          <div class="d-flex align-center ga-2">
            <AxoSparkline v-if="mdAndUp && spark[item.function_id]" :values="spark[item.function_id]!" :height="26" style="width: 72px" />
            <div class="text-body-2 text-no-wrap">
              {{ formatNumber(day[item.function_id]?.attempts ?? 0) }}
              <span v-if="failPct(item.function_id)" class="text-caption text-error">· {{ failPct(item.function_id) }} failed</span>
            </div>
          </div>
        </template>
        <template #item.last="{ item }">
          <span v-if="lastRun[item.function_id]" class="text-body-2 text-no-wrap" :title="formatExactTime(lastRun[item.function_id])">
            {{ timeAgo(lastRun[item.function_id]) }}</span>
          <span v-else class="axo-muted">—</span>
        </template>
        <template #item.by="{ item }"><span class="text-body-2 text-no-wrap">{{ item.latest.registered_by_name ?? '—' }}</span></template>
        <template #item.actions="{ item }">
          <v-btn v-if="can('developer') && !item.deleted" size="small" variant="tonal" color="primary" class="text-none"
                 prepend-icon="mdi-play" :to="`/ves/${id}/functions/${item.function_id}/run`" @click.stop>Run</v-btn>
        </template>
      </AxoDataTable>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { ChartAnswer, TopAnswer } from 'axo-ui-kit/app/types/cp'
import type { FunctionRow } from '~/utils/functions'
import type { JobListRow } from '~/utils/jobs'

// The VE's functions, one row per function (its newest version): how it
// runs, its state, its jobs of the last 24 hours (count, failures, a trend
// line), its last run. Live enough: read again every 15 s.
const { id, ve, can, vemUp, cp, api } = useCurrentVe()
const functions = useFunctions()
const { mdAndUp } = useDisplay()          // the trend column only shows on wide screens
useHead(() => ({ title: `Functions · ${ve.value?.name ?? 'VE'} · axo` }))

const route = useRoute()
const router = useRouter()
const search = ref((route.query.q as string) ?? '')
const runtime = ref<string | null>((route.query.runtime as string) ?? null)
const state = ref<string | null>((route.query.state as string) ?? null)
const showDeleted = ref(route.query.deleted === '1')
watch([search, runtime, state, showDeleted], () => router.replace({ query: { ...route.query, q: search.value || undefined,
  runtime: runtime.value || undefined, state: state.value || undefined, deleted: showDeleted.value ? '1' : undefined } }))

const RUNTIMES = [{ value: 'process', title: 'Simple' }, { value: 'container', title: 'Container' }]
const STATES = [{ value: 'ready', title: 'Ready' }, { value: 'starting', title: 'Starting' },
  { value: 'stopped', title: 'Stopped' }, { value: 'crashed', title: 'Crashed' }]

const shown = computed(() => functions.rows.value.filter((f: FunctionRow) =>
  (showDeleted.value || !f.deleted)
  && (!search.value || f.name.toLowerCase().includes(search.value.toLowerCase()))
  && (!runtime.value || (f.latest.runtime_spec?.type ?? 'process') === runtime.value)
  && (!state.value || functionState(f.latest, f.deleted) === state.value)))

// Wide screens only (CSS, so the server and the browser render the same).
const WIDE = { headerProps: { class: 'd-none d-md-table-cell' }, cellProps: { class: 'd-none d-md-table-cell' } }
const headers = [
  { title: 'Name', key: 'name', sortable: true },
  { title: 'Version', key: 'version', sortable: false, ...WIDE },
  { title: 'Runs as', key: 'runtime', sortable: false, ...WIDE },
  { title: 'State', key: 'state', sortable: false },
  { title: 'Jobs (24 h)', key: 'jobs', sortable: false, ...WIDE },
  { title: 'Last run', key: 'last', sortable: false },
  { title: 'Registered by', key: 'by', sortable: false, headerProps: { class: 'd-none d-lg-table-cell' }, cellProps: { class: 'd-none d-lg-table-cell' } },
  { title: '', key: 'actions', sortable: false, align: 'end' as const },
]

// ── the last 24 hours ──────────────────────────────────────────────────────
const day = ref<Record<string, { attempts: number; failed: number }>>({})
const spark = ref<Record<string, number[]>>({})
const lastRun = ref<Record<string, number>>({})
async function loadFigures() {
  if (!vemUp.value) return
  const end = Date.now() / 1000
  const start = end - 86400
  const q = (o: Record<string, string | number>) => new URLSearchParams(Object.entries({ start, end, ...o })
    .map(([k, v]) => [k, String(v)])).toString()
  const [top, chart, recent] = await Promise.all([
    cp<TopAnswer>(`/stats/top?${q({ by: 'function', limit: 200 })}`).catch(() => null),
    cp<ChartAnswer>(`/stats/chart?${q({ metric: 'job.total_ms', group_by: 'function', statistics: 'count', step_s: 3600 })}`).catch(() => null),
    api<{ rows: JobListRow[] }>('/jobs?limit=200').catch(() => null),
  ])
  if (top) day.value = Object.fromEntries(top.rows.map(r => [r.function_id!, { attempts: r.attempts, failed: r.failed }]))
  if (chart) {
    const hours = [...new Set(chart.points.map(p => p.t))].sort((a, b) => a - b)
    const by: Record<string, Record<number, number>> = {}
    for (const p of chart.points) (by[p.group ?? ''] ??= {})[p.t] = p.values.count ?? 0
    spark.value = Object.fromEntries(Object.entries(by).map(([g, v]) => [g, hours.map(h => v[h] ?? 0)]))
  }
  if (recent) {
    const last: Record<string, number> = {}
    for (const row of recent.rows) {
      for (const j of row.type === 'job' ? [row.job] : row.jobs) {
        const t = ts(j.created_at)
        if (t && (!last[j.function_id] || t > last[j.function_id]!)) last[j.function_id] = t
      }
    }
    lastRun.value = last
  }
}
const failPct = (fid: string) => {
  const d = day.value[fid]
  return d && d.attempts && d.failed ? formatPercent((100 * d.failed) / d.attempts) : null
}

usePolling(() => Promise.all([functions.load(true), loadFigures()]), 15_000)
watch([id, vemUp], () => { functions.load(true); loadFigures() })
</script>

<style scoped>
.axo-filter { flex: 0 1 220px; min-width: 160px; }
@media (max-width: 600px) { .axo-filter { flex-basis: 100%; } }
</style>
