<template>
  <div>
    <template v-if="doc">
    <v-alert v-for="w in doc.warnings" :key="w" type="warning" variant="tonal" density="compact" class="mb-3">{{ w }}</v-alert>
    <v-row>
      <v-col cols="12" lg="8">
        <div class="d-flex align-center flex-wrap mb-2">
          <span class="text-subtitle-1 font-weight-medium text-no-wrap">The steps</span>
          <span v-if="lastRun" class="text-caption axo-muted ml-2 d-none d-sm-inline">colored by the last run ({{ timeAgo(ts(lastRun.queued_at)) }})</span>
          <v-spacer />
          <v-btn v-if="lastRun" size="small" variant="text" :to="`${base}/runs/${lastRun.run_id}`" append-icon="mdi-arrow-right">Last run</v-btn>
        </div>
        <ChoreographyCanvas :graph="graph" :canvas-id="`ch-overview-${doc.choreography_id}`" :run="lastRun" :fname="nameOf" height="440px"
                            :minimap="false" :answer-step="doc.settings.answer_step ?? null" @pick="onPick" />
      </v-col>
      <v-col cols="12" lg="4">
        <AxoPanel title="Facts" icon="mdi-information-outline" class="mb-4" stacked>
          <AxoKeyValue :items="facts" :columns="2" />
        </AxoPanel>
        <AxoPanel v-if="doc.uses?.length || doc.used_by?.length" title="Calls between choreographies" icon="mdi-layers-triple-outline" class="mb-4" stacked
                  help="A choreography step runs another choreography as its own run, linked to this one. One that others use can't be deleted until they stop using it.">
          <template v-if="doc.uses?.length">
            <div class="text-caption axo-muted mb-1">Uses</div>
            <div v-for="u in doc.uses" :key="u.step" class="axo-ch-call">
              <v-icon icon="mdi-arrow-right" size="14" class="mr-1" />
              <NuxtLink v-if="u.exists" class="axo-link" :to="chLink(u.name)">{{ u.name }}</NuxtLink>
              <span v-else class="text-error">{{ u.name || u.choreography_id }} (deleted)</span>
              <span class="text-caption axo-muted ml-1">· step {{ u.step }} · {{ u.version ? `v${u.version}` : `newest${u.latest_version ? ` (v${u.latest_version})` : ''}` }}</span>
            </div>
          </template>
          <template v-if="doc.used_by?.length">
            <div class="text-caption axo-muted mb-1" :class="{ 'mt-3': doc.uses?.length }">Used by</div>
            <div v-for="u in doc.used_by" :key="u.choreography_id" class="axo-ch-call">
              <v-icon icon="mdi-arrow-left" size="14" class="mr-1" />
              <NuxtLink class="axo-link" :to="chLink(u.name)">{{ u.name }}</NuxtLink>
              <span class="text-caption axo-muted ml-1">· step {{ u.steps.join(', ') }}{{ u.pinned.length ? ` · pinned to ${u.pinned.map(v => `v${v}`).join(', ')}` : ' · newest' }}</span>
            </div>
          </template>
        </AxoPanel>
        <AxoPanel title="Last 24 hours" icon="mdi-chart-box-outline" stacked>
          <div class="d-flex flex-wrap ga-4">
            <div><div class="text-h6">{{ formatNumber(usage?.runs ?? 0) }}</div><div class="text-caption axo-muted">runs</div></div>
            <div><div class="text-h6">{{ usage?.success_rate != null ? `${Math.round(usage.success_rate * 100)} %` : '—' }}</div><div class="text-caption axo-muted">completed well</div></div>
            <div><div class="text-h6">{{ usage?.duration_ms?.p50 != null ? formatMs(usage.duration_ms.p50) : '—' }}</div><div class="text-caption axo-muted">typical run</div></div>
            <div><div class="text-h6">{{ usage?.duration_ms?.p95 != null ? formatMs(usage.duration_ms.p95) : '—' }}</div><div class="text-caption axo-muted">slowest 5 %</div></div>
          </div>
          <div v-if="usage?.by_status && Object.keys(usage.by_status).length" class="d-flex flex-wrap ga-2 mt-3">
            <AxoStatusChip v-for="(n, s) in usage.by_status" :key="s" kind="run" :value="String(s)" :label="`${stepLook(String(s)).label} · ${n}`" />
          </div>
        </AxoPanel>
      </v-col>
    </v-row>

    <div class="d-flex align-center mt-6 mb-2">
      <span class="text-subtitle-1 font-weight-medium">Newest runs</span><v-spacer />
      <v-btn size="small" variant="text" :to="`${base}/runs`" append-icon="mdi-arrow-right">Every run</v-btn>
    </div>
    <ChoreographyRunsTable :rows="runs" :base="base" :loading="loading" />
    </template>
  </div>
</template>

<script setup lang="ts">
// A choreography at a glance: its graph colored by the last run, facts
// (version, steps, triggers, settings), the choreographies it calls / that
// call it, the last 24 hours, the newest runs and its warnings. Clicking a step opens the designer.
import type { CanvasPick } from '~/components/choreography/Canvas.vue'
import type { ChGraph, Run, RunSummary } from '~/utils/choreographies'

const { id, api, vemUp } = useCurrentVe()
const { name, doc, versions } = useCurrentChoreography()
const { nameOf, load: loadFunctions } = useFunctions()
const base = computed(() => `/ves/${id.value}/choreographies/${encodeURIComponent(name.value)}`)
const graph = computed<ChGraph>(() => {
  const g = doc.value ? { ...doc.value.graph, triggers: doc.value.graph.triggers ?? doc.value.triggers } : { nodes: [], edges: [] }
  return needsLayout(g) ? arrange(g) : g
})

const runs = ref<RunSummary[]>([])
const lastRun = ref<Run | null>(null)
const usage = ref<any>(null)
const loading = ref(false)
async function load() {
  if (!vemUp.value || !name.value) return
  loading.value = true
  const enc = encodeURIComponent(name.value)
  try {
    const [list, u] = await Promise.all([api<RunSummary[]>(`/choreographies/${enc}/runs`, { query: { limit: 5 } }),
      api(`/choreographies/${enc}/usage`, { query: { since_minutes: 1440 } }).catch(() => null)])
    runs.value = list
    usage.value = u
    lastRun.value = list[0] ? await api<Run>(`/choreographies/${enc}/runs/${list[0].run_id}`) : null
  } catch { /* the frame shows the error */ } finally { loading.value = false }
}
onMounted(() => { load(); loadFunctions() })
watch([name, vemUp], load)
usePolling(load, 15_000)
useVeLive(() => (vemUp.value ? 'choreographies' : null), (m: any) => { if (!m?.choreography_id || m.choreography_id === doc.value?.choreography_id) load() })

const facts = computed(() => {
  const d = doc.value
  if (!d) return []
  return [
    { label: 'Version', value: `v${d.version} of ${versions.value.length || d.version}` },
    { label: 'Steps', value: d.graph.nodes.length },
    { label: 'Triggers', value: d.triggers.length ? d.triggers.map(t => t.event_type === 'file.written' ? `file in ${t.bucket}` : t.event_type).join(', ') : 'none (by hand)' },
    { label: 'At the same time', value: `${d.settings.max_parallel_runs} runs` },
    { label: 'After an error', value: d.settings.allow_completed_with_errors ? 'keeps going' : 'stops the run' },
    { label: 'Step answers kept', value: keepTitle(d.settings.keep_results) },
    ...((d.ends?.length ?? 0) > 1 || d.used_by?.length ? [{ label: 'Its answer', value: d.settings.answer_step ? `step ${d.settings.answer_step}`
      : d.ends?.length === 1 ? `step ${d.ends[0]}` : 'not picked' }] : []),
  ]
})
const chLink = (n: string) => `/ves/${id.value}/choreographies/${encodeURIComponent(n)}`
function onPick(p: CanvasPick) {
  if (p?.kind === 'node' && lastRun.value) navigateTo(`${base.value}/runs/${lastRun.value.run_id}?step=${encodeURIComponent(p.id)}`)
}
</script>

<style scoped>
.axo-ch-call { display: flex; align-items: center; flex-wrap: wrap; font-size: 0.875rem; padding: 2px 0; }
</style>
