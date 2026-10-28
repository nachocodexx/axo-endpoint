<template>
  <div>
    <AxoErrorAlert v-if="error" :error="error" title="The run couldn't load" :retry="load" class="mb-4" />
    <AxoSkeleton v-else-if="!run" :lines="8" />
    <template v-else>
      <v-alert v-if="run.parent" type="info" variant="tonal" density="compact" icon="mdi-layers-triple-outline" class="mb-3">
        Part of <NuxtLink class="axo-link" :to="parentLink">run {{ shortId(run.parent.run_id) }} of {{ run.parent.choreography_name }}</NuxtLink>
        · step {{ run.parent.step }}<template v-if="run.parent.item != null"> · file {{ run.parent.item + 1 }}</template>.
        It acts for the same person and is resumed or cancelled with that run.
      </v-alert>
      <div class="d-flex align-center flex-wrap ga-2 mb-2">
        <v-btn icon="mdi-arrow-left" size="small" variant="text" :to="`${base}/runs`" aria-label="Every run" />
        <AxoStatusChip kind="run" :value="shown!.status" />
        <AxoCopy :text="run.run_id" max-width="200px" />
        <v-chip size="small" variant="tonal">v{{ run.version }}</v-chip>
        <span class="text-body-2 axo-muted">{{ startedBy }}</span>
        <span class="text-body-2"><v-icon icon="mdi-timer-outline" size="16" /> {{ duration }}</span>
        <span v-if="cost !== null" class="text-body-2"><v-icon icon="mdi-cash" size="16" /> {{ formatUcents(cost) }}</span>
        <v-spacer />
        <v-btn v-if="finished && !replaying" variant="text" class="text-none" prepend-icon="mdi-replay" @click="replay">Replay</v-btn>
        <v-btn v-if="replaying" variant="text" class="text-none" prepend-icon="mdi-stop" @click="stopReplay">Stop replay</v-btn>
        <template v-if="can('developer')">
          <v-btn v-if="runActive(run.status)" variant="tonal" color="error" class="text-none" prepend-icon="mdi-cancel" :loading="busy === 'cancel'" @click="cancel">Cancel</v-btn>
          <v-btn v-if="resumable" variant="tonal" class="text-none" prepend-icon="mdi-play-pause" :loading="busy === 'resume'" @click="resume">Resume</v-btn>
          <v-btn variant="text" class="text-none" prepend-icon="mdi-play" :to="`${base}?run=again`">Run again…</v-btn>
        </template>
      </div>
      <div class="d-flex flex-wrap ga-2 mb-3">
        <v-chip v-for="(n, s) in counts" :key="s" size="small" variant="tonal" :color="stepLook(String(s)).color"
                :prepend-icon="stepLook(String(s)).ring ? undefined : stepLook(String(s)).icon">
          <AxoRunningRing v-if="stepLook(String(s)).ring" :size="12" class="me-2" />
          {{ n }} {{ stepLook(String(s)).label.toLowerCase() }}
        </v-chip>
        <v-chip v-if="run.resumes" size="small" variant="outlined">resumed {{ run.resumes }}×</v-chip>
        <v-chip v-if="run.stopped_by" size="small" variant="tonal" color="error">stopped by {{ title(run.stopped_by) }}</v-chip>
        <v-chip v-if="replaying" size="small" color="primary" variant="flat" prepend-icon="mdi-replay">Replaying</v-chip>
      </div>
      <v-alert v-if="!graph" type="info" variant="tonal" density="compact">Version {{ run.version }} couldn't load.</v-alert>
      <ChoreographyCanvas v-else ref="canvas" :graph="graph" :canvas-id="`ch-run-${run.run_id}`" :run="shown" :live="watching" :now="now" :fname="nameOf"
:carried="carried" :answer-step="answerStep" :selected="picked?.kind === 'edge' ? `edge:${picked.id}` : picked?.id ?? null" height="max(440px, calc(100vh - 420px))"
                          @pick="onPick" />
      <ChoreographyStepPanel v-if="graph" :open="picked" :run="shown!" :graph="graph" :fname="nameOf" :title="title" @close="picked = null" />
    </template>
  </div>
</template>

<script setup lang="ts">
// One run, live: the graph with each step's state (queued pulse, running
// ring + timer, files done, retries, pop when done, shake when failed),
// arrows flowing while their step runs and a dot travelling along each one
// followed. Click a step or arrow for its details. Finished runs can be
// replayed (about 10 seconds, from the real start / finish times).
import type { CanvasPick } from '~/components/choreography/Canvas.vue'
import type { ChGraph, Run, StepState } from '~/utils/choreographies'

const { id, api, can, vemUp } = useCurrentVe()
const { name, doc, version } = useCurrentChoreography()
const { nameOf, load: loadFunctions } = useFunctions()
const notifier = useNotifier()
const route = useRoute()
const runId = computed(() => String(route.params.runId))
const base = computed(() => `/ves/${id.value}/choreographies/${encodeURIComponent(name.value)}`)

const run = ref<Run | null>(null)
const graph = ref<ChGraph | null>(null)
const error = ref<unknown>(null)
async function load() {
  if (!vemUp.value) return
  try {
    const r = await api<Run>(`/choreographies/${encodeURIComponent(name.value)}/runs/${runId.value}`)
    run.value = r
    error.value = null
    const key = `${r.choreography_id}:${r.version}`
    if (!graph.value || graphKey !== key) {
      const d = doc.value?.version === r.version ? doc.value : await version(r.version)
      const g = { ...d.graph, triggers: d.graph.triggers ?? [] }
      answerStep.value = d.settings?.answer_step ?? null
      graph.value = needsLayout(g) ? arrange(g) : g
      graphKey = key
    }
  } catch (err) { error.value = err }
}
let graphKey = ''
onMounted(() => { load(); loadFunctions() })
watch([runId, vemUp], load)
// the page is reused when a link opens another run (a called run, its caller)
watch(runId, () => {
  run.value = null; error.value = null; cost.value = null; stopReplay()
  picked.value = route.query.step ? { kind: 'node', id: String(route.query.step) } : null
})
watch(() => run.value?.run_id, (now) => { if (now) loadCost() })
const active = computed(() => runActive(run.value?.status))
const finished = computed(() => !!run.value && !active.value)
usePolling(() => (active.value ? load() : undefined), 2000)
let liveTimer: ReturnType<typeof setTimeout> | undefined
useVeLive(() => (vemUp.value ? `choreographies:${runId.value}` : null), () => { clearTimeout(liveTimer); liveTimer = setTimeout(load, 150) })
const watching = computed(() => active.value || replaying.value)

// a ticking clock for running steps' timers
const now = ref(Date.now())
let clock: ReturnType<typeof setInterval> | undefined
onMounted(() => { clock = setInterval(() => { if (watching.value) now.value = Date.now() }, 1000) })
onBeforeUnmount(() => { clearInterval(clock); stopReplay() })

const { load: loadChoreographies, byId: chById } = useChoreographies()
onMounted(() => loadChoreographies())
const parentLink = computed(() => {
  const p = run.value?.parent
  if (!p) return ''
  return `/ves/${id.value}/choreographies/${encodeURIComponent(chById(p.choreography_id)?.name ?? p.choreography_name)}/runs/${p.run_id}?step=${encodeURIComponent(p.step)}`
})
const shortId = (v: string) => (v.length > 14 ? `${v.slice(0, 12)}…` : v)
const answerStep = ref<string | null>(null)
const title = (nodeId: string) => { const n = graph.value?.nodes.find(x => x.node_id === nodeId); return n ? nodeTitle(n, nameOf) : nodeId }
const startedBy = computed(() => {
  const r = run.value
  if (!r) return ''
  if (r.started_by_type === 'trigger') {
    const t = r.trigger ?? {}
    return t.kind === 'file.written' ? `started by a new file: ${t.bucket}/${t.name}` : `started by the event ${t.kind}`
  }
  if (r.started_by_type === 'choreography' && r.parent) return `started by ${r.parent.choreography_name}${r.started_by_name ? ` (for ${r.started_by_name})` : ''} · ${timeAgo(ts(r.queued_at))}`
  return `started by ${r.started_by_name || (r.started_by_type === 'api_key' ? 'an API key' : r.started_by_id || 'someone')} · ${timeAgo(ts(r.queued_at))}`
})
const duration = computed(() => {
  const r = shown.value
  if (!r) return '—'
  if (r.duration_ms != null && !runActive(r.status)) return formatMs(r.duration_ms)
  const from = r.started_at ?? r.queued_at
  return from ? `${formatMs(Math.max(0, now.value - Date.parse(from)))}…` : '—'
})
const counts = computed(() => {
  const out: Record<string, number> = {}
  for (const s of Object.values(shown.value?.steps ?? {})) out[s.status] = (out[s.status] ?? 0) + 1
  return out
})
const resumable = computed(() => ['failed', 'completed_with_errors', 'cancelled'].includes(run.value?.status ?? ''))
// what travelled along each followed arrow: the size of the answer, or its files
const carried = computed(() => {
  const out: Record<string, string> = {}
  for (const e of graph.value?.edges ?? []) {
    const s = shown.value?.steps?.[e.source_node_id]
    if (!s) continue
    out[e.edge_id] = e.on_failure ? (s.error_name ?? 'the error') : s.files?.length ? `${s.files.length} file${s.files.length === 1 ? '' : 's'}`
      : s.items?.length ? `${s.items.length} answers` : formatBytes(s.output_bytes)
  }
  return out
})

// the side panel (?step= opens one)
const picked = ref<CanvasPick>(route.query.step ? { kind: 'node', id: String(route.query.step) } : null)
function onPick(p: CanvasPick) {
  picked.value = p?.kind === 'trigger' ? null : p
  // not router.replace: a route change closes Vuetify's temporary drawer
  const url = new URL(window.location.href)
  if (p?.kind === 'node') url.searchParams.set('step', p.id)
  else url.searchParams.delete('step')
  window.history.replaceState(window.history.state, '', url)
}

// cost (VE Owner / Admin)
const cost = ref<number | null>(null)
const { cp } = useCurrentVe()
async function loadCost() {
  const r = run.value
  if (!can('admin') || !r) return
  try {
    const c = await cp<{ total: { cost_ucents: number; attempts: number } }>(`/costs/choreographies/${r.choreography_id}`, { query: { run_id: r.run_id } })
    cost.value = c.total.attempts ? c.total.cost_ucents : null
  } catch { cost.value = null }
}
watch(active, (now, before) => { if (before && !now) loadCost() })     // it just finished

// cancel / resume
const busy = ref<string | null>(null)
async function cancel() {
  busy.value = 'cancel'
  try { await api(`/choreographies/${encodeURIComponent(name.value)}/runs/${runId.value}/cancel`, { method: 'POST' }); await load() }
  catch (err) { notifier.error(err, "The run couldn't be cancelled") } finally { busy.value = null }
}
async function resume() {
  busy.value = 'resume'
  try { await api(`/choreographies/${encodeURIComponent(name.value)}/runs/${runId.value}/resume`, { method: 'POST' }); notifier.success('Resumed: the failed and skipped steps run again'); await load() }
  catch (err) { notifier.error(err, "The run couldn't be resumed") } finally { busy.value = null }
}

// ── replay: the real times squeezed into about 10 seconds ──────────────────
const replaying = ref(false)
const replayAt = ref(0)          // ms since the run started, in the run's own time
let replayTimer: ReturnType<typeof setInterval> | undefined
const span = computed(() => {
  const r = run.value
  if (!r) return { start: 0, end: 0 }
  const times = Object.values(r.steps).flatMap(s => [s.started_at, s.finished_at]).filter(Boolean).map(t => Date.parse(t!))
  const start = Date.parse(r.started_at ?? r.queued_at ?? '') || Math.min(...times)
  return { start, end: Math.max(start + 1, ...times, Date.parse(r.finished_at ?? '') || 0) }
})
function replay() {
  if (!run.value) return
  replayAt.value = -400
  replaying.value = true
  const total = span.value.end - span.value.start
  const step = total / (10_000 / 50)
  clearInterval(replayTimer)
  replayTimer = setInterval(() => {
    replayAt.value += replayAt.value < 0 ? 50 : step
    now.value = span.value.start + Math.max(0, replayAt.value)
    if (replayAt.value > total + step * 30) stopReplay()
  }, 50)
}
function stopReplay() { clearInterval(replayTimer); replaying.value = false; now.value = Date.now() }
function stepAt(s: StepState, at: number): StepState {
  const started = s.started_at ? Date.parse(s.started_at) : null
  const ended = s.finished_at ? Date.parse(s.finished_at) : null
  if (s.status === 'not_taken' || s.status === 'skipped') {
    const upstream = Math.max(0, ...Object.values(run.value!.steps).map(x => (x.finished_at ? Date.parse(x.finished_at) : 0)).filter(t => t <= (ended ?? Infinity)))
    return at >= (ended ?? upstream) ? s : { ...s, status: 'pending' }
  }
  if (started === null || at < started) return { ...s, status: 'pending', attempt: 1, items: s.items.map(i => ({ ...i, status: 'pending' })) }
  if (ended === null || at < ended) return { ...s, status: 'running', duration_ms: null, items: s.items.map(i => ({ ...i, status: i.duration_ms != null && started + (i.duration_ms ?? 0) <= at ? i.status : 'running' })) }
  return s
}
const shown = computed<Run | null>(() => {
  const r = run.value
  if (!r || !replaying.value) return r
  const at = span.value.start + replayAt.value
  const steps = Object.fromEntries(Object.entries(r.steps).map(([k, s]) => [k, stepAt(s, at)]))
  const edge_states: Record<string, string> = {}
  for (const e of graph.value?.edges ?? []) {
    const src = r.steps[e.source_node_id]
    if (src?.finished_at && Date.parse(src.finished_at) <= at && r.edge_states[e.edge_id]) edge_states[e.edge_id] = r.edge_states[e.edge_id]!
  }
  const done = at >= span.value.end
  return { ...r, steps, edge_states, status: replayAt.value < 0 ? 'queued' : done ? r.status : 'running', duration_ms: done ? r.duration_ms : null }
})
</script>
