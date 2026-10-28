<template>
  <div class="axo-designer" :class="{ 'is-readonly': readonly }">
    <!-- the bar -->
    <div class="axo-designer__bar">
      <template v-if="!readonly">
        <v-btn icon="mdi-undo" size="small" variant="text" :disabled="!draft.canUndo.value" aria-label="Undo (Ctrl+Z)" title="Undo (Ctrl+Z)" @click="draft.undo()" />
        <v-btn icon="mdi-redo" size="small" variant="text" :disabled="!draft.canRedo.value" aria-label="Redo (Ctrl+Shift+Z)" title="Redo (Ctrl+Shift+Z)" @click="draft.redo()" />
        <v-btn size="small" variant="text" prepend-icon="mdi-auto-fix" @click="arrangeAll">Arrange</v-btn>
        <v-divider vertical class="mx-1" />
      </template>
      <v-chip v-if="readonly && readonlyReason" size="small" variant="tonal" prepend-icon="mdi-eye-outline">{{ readonlyReason }}</v-chip>
      <v-menu v-if="problems.length" location="bottom">
        <template #activator="{ props: p }">
          <v-chip v-bind="p" size="small" :color="errors.length ? 'error' : 'warning'" variant="tonal"
                  :prepend-icon="errors.length ? 'mdi-alert-circle' : 'mdi-alert'" class="ml-1">
            {{ errors.length ? `${errors.length} problem${errors.length === 1 ? '' : 's'}` : `${problems.length} warning${problems.length === 1 ? '' : 's'}` }}
          </v-chip>
        </template>
        <v-list density="compact" max-width="460">
          <v-list-item v-for="p in problems" :key="p.message" :prepend-icon="p.warning ? 'mdi-alert' : 'mdi-alert-circle'"
                       :base-color="p.warning ? 'warning' : 'error'" :title="p.message" class="axo-wrap" @click="focusProblem(p)" />
        </v-list>
      </v-menu>
      <v-progress-circular v-if="checking" indeterminate size="16" width="2" class="ml-2" />
      <v-spacer />
      <Transition name="axo-fade">
        <span v-if="draft.dirty.value" class="axo-designer__dirty"><v-icon icon="mdi-circle" size="8" color="warning" class="mr-1" />Unsaved changes · kept in this browser</span>
      </Transition>
      <template v-if="!readonly">
        <v-btn v-if="draft.dirty.value" size="small" variant="text" @click="discard">Discard</v-btn>
        <AxoBtn size="small" variant="flat" :loading="saving" :disabled="!canSave" :title="saveHint" @click="save()">
          {{ doc ? `Save as v${(doc.latest_version ?? doc.version) + 1}` : 'Create' }}
        </AxoBtn>
      </template>
    </div>
    <div v-if="restored" class="axo-designer__note">
      <v-icon icon="mdi-history" size="16" class="mr-1" /> Your unsaved changes from {{ timeAgo(draft.savedAt.value ?? Date.now()) }} were brought back.
      <v-btn size="x-small" variant="text" @click="restored = false">OK</v-btn>
    </div>

    <div class="axo-designer__main">
      <ChoreographyAddPanel v-if="!readonly" v-model:folded="panelFolded" :functions="functions" :objects="objects" :methods-of="methodsOf"
                            :buckets="buckets" :choreographies="chRows"
                            :blocked="blocked" @add="addFromPanel" />
      <ChoreographyCanvas v-if="graph" ref="canvas" :graph="graph" :canvas-id="canvasId" :editable="!readonly" :fname="fname"
                          :problems="problems" :selected="selected" :height="height" :answer-step="draft.content.value?.settings?.answer_step ?? null" class="flex-grow-1"
                          empty-text="Drag a function, object, choreography or bucket from the left to start." @pick="onPick" @move="onMove"
                          @connect="onConnect" @drop="onDrop" />
    </div>

    <ChoreographyStepDialog :node="openNode" :graph="graph ?? EMPTY" :info="info" :step="openNode ? steps[openNode.node_id] ?? null : null"
                            :readonly="readonly" :functions="functions" :objects="objects" :methods-of="methodsOf" :params-of="paramsOf"
                            :buckets="buckets" :last-run="openNode ? lastRun?.steps?.[openNode.node_id] ?? null : null"
                            :problems="openNode ? problemsOf(openNode.node_id) : []" :fname="fname" :title="title" :self-id="doc?.choreography_id ?? null"
                            @close="openNode = null" @save="saveNode" @remove="removeNode" />
    <ChoreographyArrowDialog :edge="openEdge" :graph="graph ?? EMPTY" :info="info" :readonly="readonly" :sample="sample" :title="title"
                             @close="openEdge = null" @save="saveEdge" @remove="removeEdge" />
    <ChoreographyTriggerDialog :trigger="openTrigger?.t ?? null" :graph="graph ?? EMPTY" :info="info" :event-types="eventTypes"
                               :suggestions="suggestions" :buckets="buckets" :readonly="readonly" :title="title"
                               @close="openTrigger = null" @save="saveTrigger" @remove="removeTrigger" />

    <!-- someone saved meanwhile -->
    <v-dialog v-model="conflict.open" max-width="520">
      <v-card title="Someone saved a newer version">
        <v-card-text>
          {{ conflict.by || 'Someone' }} saved v{{ conflict.version }} after you started editing v{{ draft.base.value }}.
          You can open theirs (your changes are dropped) or save yours anyway as v{{ conflict.version + 1 }}.
        </v-card-text>
        <v-card-actions>
          <v-btn variant="text" @click="openTheirs">Open theirs</v-btn>
          <v-spacer />
          <v-btn variant="text" @click="conflict.open = false">Cancel</v-btn>
          <v-btn color="primary" variant="flat" @click="save(true)">Save mine anyway as v{{ conflict.version + 1 }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
// The designer: the draft graph on the canvas, the "Add a step" panel, the
// step / arrow / trigger dialogs, a live check of the draft (problems block
// saving, warnings don't), undo / redo, Arrange (dagre), and saving as the
// next version (or creating, on the new page). Unsaved changes stay in this
// browser. When someone saved meanwhile: open theirs or save mine anyway.
import { useDisplay } from 'vuetify'
import type { CanvasPick } from './Canvas.vue'
import type { AddPayload } from './AddPanel.vue'
import type { ChEdge, ChGraph, ChNode, ChSettings, ChTrigger, CheckAnswer, Choreography, DesignerInfo, EventType, Problem, Run, StepInfo, Suggestions } from '~/utils/choreographies'
import type { ParamSpec } from '~/utils/functions'
import type { ActiveObject } from '~/utils/objects'

const props = withDefaults(defineProps<{ doc: Choreography | null; readonly?: boolean; readonlyReason?: string; height?: string
  /** The new page: what to create. */ newName?: string; newDescription?: string; newSettings?: ChSettings
  /** "Edit from this version": its graph becomes the draft (an undoable change). */ seed?: Choreography | null }>(),
{ height: 'calc(100vh - 290px)' })
const emit = defineEmits<{ (e: 'saved', doc: Choreography): void }>()

const { id: veId, api } = useCurrentVe()
const notifier = useNotifier()
const { confirm } = useConfirm()
const { rows: functionRows, load: loadFunctions, nameOf } = useFunctions()
const { objects, load: loadObjects, versionOf } = useObjects()
const { designer, suggestions: loadSuggestions, rows: chRows, load: loadChoreographies } = useChoreographies()
const { smAndDown, xs } = useDisplay()

const EMPTY: ChGraph = { nodes: [], edges: [], triggers: [] }
const canvasId = `ch-designer-${props.doc?.choreography_id ?? 'new'}`
const draft = useChoreographyDraft(() => `${veId.value}/${props.doc?.choreography_id ?? '__new__'}`)
const graph = computed(() => draft.content.value?.graph ?? null)
const readonly = computed(() => props.readonly || xs.value)
const readonlyReason = computed(() => (props.readonly ? props.readonlyReason : xs.value ? 'View only on a phone' : ''))
const panelFolded = ref(smAndDown.value)
const restored = ref(false)
const canvas = ref<any>(null)

// ── what the dialogs offer ──────────────────────────────────────────────────
const info = ref<DesignerInfo | null>(null)
const suggestions = ref<Suggestions | null>(null)
const eventTypes = ref<EventType[]>([])
const bucketNames = ref<string[]>([])
const functions = computed(() => functionRows.value.filter(f => f.latest?.runtime_spec?.call_style !== 'object'))
const buckets = computed(() => [...new Set([...bucketNames.value, ...(graph.value?.nodes ?? []).filter(n => n.bucket_name).map(n => n.bucket_name!)])])
const fname = (id: string) => nameOf(id) || id
// a choreography step may not call this one, nor one that calls it back
const blocked = computed(() => loopMakers(chRows.value, props.doc?.choreography_id ?? null))
const methodsOf = (o: ActiveObject) => {
  const v = versionOf(o.class_name, o.class_version)
  const names = (v?.methods ?? []).map(m => m.name)
  return names.length ? names : ['run']
}
const paramsOf = (o: ActiveObject, method: string): ParamSpec[] | null => {
  const m = versionOf(o.class_name, o.class_version)?.methods.find(x => x.name === method)
  if (!m) return null
  return m.params_schema ?? m.params.map(p => ({ name: p, type: 'json', required: !(m.optional ?? []).includes(p), default: m.defaults?.[p] }))
}
const title = (nodeId: string) => {
  const n = graph.value?.nodes.find(x => x.node_id === nodeId)
  return n ? nodeTitle(n, fname) : nodeId
}

const { apiFetch } = useApi()
onMounted(async () => {
  start()
  loadFunctions(); loadObjects(); loadChoreographies()
  const [i, s, types, bks] = await Promise.all([
    designer().catch(() => null), loadSuggestions(),
    api<EventType[]>('/event-types').catch(() => []),
    apiFetch<Array<{ name: string }>>('/buckets').catch(() => []),
  ])
  info.value = i; suggestions.value = s; eventTypes.value = types; bucketNames.value = bks.map(b => b.name)
  loadLastRun()
})

function start() {
  const doc = props.doc
  let g: ChGraph = doc ? JSON.parse(JSON.stringify(doc.graph)) : { nodes: [], edges: [], triggers: [] }
  g.triggers ??= doc?.triggers ?? []
  if (g.nodes.length && needsLayout(g)) g = arrange(g)
  restored.value = draft.start({ graph: g, name: doc?.name ?? props.newName ?? '', description: doc?.description ?? props.newDescription ?? '',
    settings: doc?.settings ?? props.newSettings ?? { ...DEFAULT_SETTINGS } }, doc?.latest_version ?? doc?.version ?? 0)
  if (props.seed) {
    const seed = props.seed
    let sg: ChGraph = JSON.parse(JSON.stringify(seed.graph))
    sg.triggers ??= []
    if (sg.nodes.length && needsLayout(sg)) sg = arrange(sg)
    draft.change((d) => { d.graph = sg; d.settings = seed.settings; d.description = seed.description ?? '' })
    notifier.info(`The designer now holds v${seed.version}: save it as the next version to make it current.`)
  }
  check()
}
watch(() => props.doc?.version, (v, before) => { if (v && before && !draft.dirty.value) start() })
// the new page's name / description / settings follow its form
watch(() => [props.newName, props.newDescription, props.newSettings], () => {
  if (props.doc || !draft.content.value) return
  draft.content.value = { ...draft.content.value, name: props.newName ?? '', description: props.newDescription ?? '', settings: props.newSettings ?? draft.content.value.settings }
}, { deep: true })

// ── the last run (previews: params last time, the arrow's sample answer) ──
const lastRun = ref<Run | null>(null)
async function loadLastRun() {
  if (!props.doc) return
  try {
    const [last] = await api<Array<{ run_id: string }>>(`/choreographies/${encodeURIComponent(props.doc.name)}/runs`, { query: { limit: 1 } })
    if (last) lastRun.value = await api<Run>(`/choreographies/${encodeURIComponent(props.doc.name)}/runs/${last.run_id}`)
  } catch { /* no runs yet, or not visible */ }
}
const sample = ref<unknown>(undefined)
async function loadSample(nodeId: string) {
  sample.value = undefined
  const job = lastRun.value?.steps?.[nodeId]?.job_id
  if (!job || lastRun.value?.steps?.[nodeId]?.status !== 'completed') return
  try { sample.value = await api(`/jobs/${job}/result`) } catch { /* result gone */ }
}

// ── live check ──────────────────────────────────────────────────────────────
const checking = ref(false)
const problems = ref<Problem[]>([])
const steps = ref<Record<string, StepInfo>>({})
let checkTimer: ReturnType<typeof setTimeout> | undefined
let checkSeq = 0
function check() {
  clearTimeout(checkTimer)
  checkTimer = setTimeout(async () => {
    const g = graph.value
    if (!g) return
    if (!g.nodes.length) { problems.value = []; steps.value = {}; return }
    const seq = ++checkSeq
    checking.value = true
    try {
      const answer = await api<CheckAnswer>('/choreographies/check', { method: 'POST', body: { graph: cleanGraph(g),
        choreography_id: props.doc?.choreography_id ?? null, settings: draft.content.value?.settings ?? null } })
      if (seq !== checkSeq) return
      problems.value = [...placeProblems(answer.problems, g), ...placeProblems(answer.warnings, g, true)]
      if (answer.ok) steps.value = answer.steps
    } catch (err: any) {
      if (seq !== checkSeq) return
      const msg = AxoApiError.from(err)
      problems.value = [{ message: msg.message }]
    } finally { if (seq === checkSeq) checking.value = false }
  }, 450)
}
watch(() => JSON.stringify(graph.value ? [cleanGraph(graph.value), draft.content.value?.settings] : null), check)
const errors = computed(() => problems.value.filter(p => !p.warning))
const problemsOf = (nodeId: string) => problems.value.filter(p => p.node_id === nodeId).map(p => p.message)
const nameProblem = computed(() => (props.doc ? undefined : choreographyNameProblem(draft.content.value?.name ?? '')))
const canSave = computed(() => !!graph.value?.nodes.length && !errors.value.length && !checking.value && !nameProblem.value
  && (draft.dirty.value || !props.doc))
const saveHint = computed(() => (!graph.value?.nodes.length ? 'Add a step first' : errors.value.length ? 'Fix the problems first'
  : nameProblem.value ? nameProblem.value : !draft.dirty.value && props.doc ? 'Nothing changed' : undefined))
function focusProblem(p: Problem) {
  if (p.node_id) { selected.value = p.node_id; canvas.value?.focus(p.node_id) }
}

// ── canvas events ───────────────────────────────────────────────────────────
const selected = ref<string | null>(null)
const openNode = ref<ChNode | null>(null)
const openEdge = ref<ChEdge | null>(null)
const openTrigger = ref<{ index: number; t: ChTrigger } | null>(null)
function onPick(p: CanvasPick) {
  if (!p) { selected.value = null; return }
  const g = graph.value!
  if (p.kind === 'node') {
    selected.value = p.id
    openNode.value = g.nodes.find(n => n.node_id === p.id) ?? null
  } else if (p.kind === 'edge') {
    selected.value = `edge:${p.id}`
    openEdge.value = g.edges.find(e => e.edge_id === p.id) ?? null
    if (openEdge.value) loadSample(openEdge.value.source_node_id)
  } else {
    selected.value = p.id
    const index = (g.triggers ?? []).findIndex((t, i) => TRIGGER_NODE(t, i) === p.id)
    if (index >= 0) openTrigger.value = { index, t: g.triggers![index]! }
  }
}
function onMove(nodeId: string, pos: { x: number; y: number }) {
  draft.change(d => { const n = d.graph.nodes.find(x => x.node_id === nodeId); if (n) n.position = pos })
}
function onConnect(source: string, target: string) {
  const g = graph.value!
  if (g.edges.some(e => e.source_node_id === source && e.target_node_id === target)) return
  const edge_id = newEdgeId(g)
  draft.change(d => { d.graph.edges.push({ edge_id, source_node_id: source, target_node_id: target }) })
}
function onDrop(payload: string, pos: { x: number; y: number }) {
  try { add(JSON.parse(payload) as AddPayload, pos) } catch { /* not ours */ }
}
function addFromPanel(p: AddPayload) {
  const after = selected.value && !selected.value.startsWith('edge:') && !selected.value.startsWith('trigger:') ? selected.value : null
  add(p, canvas.value?.placeAfter(after) ?? { x: 0, y: 0 }, after)
}
function add(p: AddPayload, pos: { x: number; y: number }, after?: string | null) {
  const g = graph.value!
  if (p.kind === 'trigger') {
    const t: ChTrigger = { event_type: p.event_type ?? '', targets: [] }
    openTrigger.value = { index: -1, t }
    return
  }
  const base = p.kind === 'function' ? p.function_name : p.kind === 'object' ? `${p.object_name}-${p.method}`
    : p.kind === 'choreography' ? p.choreography_name : p.bucket_name?.split('/').pop()
  const node: ChNode = { node_id: newNodeId(g, base ?? p.kind), kind: p.kind, position: pos }
  if (p.kind === 'function') Object.assign(node, { function_id: p.function_id, function_name: p.function_name })
  if (p.kind === 'object') Object.assign(node, { object_name: p.object_name, method: p.method === 'run' ? null : p.method })
  if (p.kind === 'choreography') Object.assign(node, { choreography_id: p.choreography_id, choreography_name: p.choreography_name })
  if (p.kind === 'bucket') node.bucket_name = p.bucket_name
  draft.change((d) => {
    d.graph.nodes.push(node)
    if (after) d.graph.edges.push({ edge_id: newEdgeId(d.graph), source_node_id: after, target_node_id: node.node_id })
  })
  selected.value = node.node_id
  nextTick(() => { openNode.value = graph.value!.nodes.find(n => n.node_id === node.node_id) ?? null })
}

// ── dialogs ─────────────────────────────────────────────────────────────────
function saveNode(n: ChNode) {
  draft.change(d => { const i = d.graph.nodes.findIndex(x => x.node_id === n.node_id); if (i >= 0) d.graph.nodes[i] = n })
  openNode.value = null
}
function removeNode(nodeId: string) {
  draft.change((d) => {
    d.graph.nodes = d.graph.nodes.filter(n => n.node_id !== nodeId)
    d.graph.edges = d.graph.edges.filter(e => e.source_node_id !== nodeId && e.target_node_id !== nodeId)
    for (const t of d.graph.triggers ?? []) t.targets = (t.targets ?? []).filter(x => x !== nodeId)
  })
  openNode.value = null
  selected.value = null
}
function saveEdge(e: ChEdge) {
  draft.change(d => { const i = d.graph.edges.findIndex(x => x.edge_id === e.edge_id); if (i >= 0) d.graph.edges[i] = e })
  openEdge.value = null
}
function removeEdge(edgeId: string) {
  draft.change(d => { d.graph.edges = d.graph.edges.filter(e => e.edge_id !== edgeId) })
  openEdge.value = null
  selected.value = null
}
function saveTrigger(t: ChTrigger) {
  const index = openTrigger.value?.index ?? -1
  draft.change((d) => {
    d.graph.triggers ??= []
    if (index >= 0) d.graph.triggers[index] = t
    else d.graph.triggers.push(t)
  })
  openTrigger.value = null
}
function removeTrigger() {
  const index = openTrigger.value?.index ?? -1
  if (index >= 0) draft.change(d => { d.graph.triggers?.splice(index, 1) })
  openTrigger.value = null
  selected.value = null
}
function arrangeAll() {
  draft.change((d) => { d.graph = arrange(d.graph) })
  setTimeout(() => canvas.value?.fit(), 60)
}

// ── keyboard ────────────────────────────────────────────────────────────────
function onKey(ev: KeyboardEvent) {
  if (readonly.value) return
  const t = ev.target as HTMLElement | null
  if (t?.closest('input, textarea, [contenteditable="true"], .v-overlay')) return
  const mod = ev.ctrlKey || ev.metaKey
  if (mod && ev.key.toLowerCase() === 'z') { ev.preventDefault(); ev.shiftKey ? draft.redo() : draft.undo() }
  else if (mod && ev.key.toLowerCase() === 'y') { ev.preventDefault(); draft.redo() }
  else if ((ev.key === 'Delete' || ev.key === 'Backspace') && selected.value) {
    ev.preventDefault()
    if (selected.value.startsWith('edge:')) removeEdge(selected.value.slice(5))
    else if (!selected.value.startsWith('trigger:')) removeNode(selected.value)
  }
}
function onUnload(ev: BeforeUnloadEvent) { if (draft.dirty.value && saving.value) ev.preventDefault() }
onMounted(() => { window.addEventListener('keydown', onKey); window.addEventListener('beforeunload', onUnload) })
onBeforeUnmount(() => { window.removeEventListener('keydown', onKey); window.removeEventListener('beforeunload', onUnload) })
onBeforeRouteLeave(async () => {
  if (!draft.dirty.value || readonly.value) return true
  return confirm({ title: 'Leave the designer?', text: 'Your unsaved changes stay in this browser; they come back when you open the designer again.',
    confirmText: 'Leave', cancelText: 'Stay' })
})

// ── save ────────────────────────────────────────────────────────────────────
const saving = ref(false)
const conflict = reactive({ open: false, version: 0, by: '' as string | null })
async function save(force = false) {
  const c = draft.content.value
  if (!c || !canSave.value) return
  saving.value = true
  try {
    if (props.doc && !force) {
      const latest = await api<Choreography>(`/choreographies/${encodeURIComponent(props.doc.choreography_id)}`)
      if (latest.version > draft.base.value) {
        Object.assign(conflict, { open: true, version: latest.version, by: latest.updated_by_name })
        return
      }
    }
    conflict.open = false
    const body = { graph: cleanGraph(c.graph), name: c.name, description: c.description || null, settings: c.settings }
    const saved = props.doc
      ? await api<Choreography & { existing: boolean }>(`/choreographies/${encodeURIComponent(props.doc.choreography_id)}`, { method: 'PUT', body })
      : await api<Choreography & { existing: boolean }>('/choreographies', { method: 'POST', body })
    draft.reset({ graph: c.graph, name: saved.name, description: saved.description ?? '', settings: saved.settings }, saved.version)
    notifier.success(saved.existing ? 'Nothing changed' : props.doc ? `Saved as v${saved.version}` : `${saved.name} created`)
    emit('saved', saved)
  } catch (err) {
    notifier.error(err, "It couldn't be saved")
  } finally { saving.value = false }
}
async function openTheirs() {
  conflict.open = false
  if (!props.doc) return
  const latest = await api<Choreography>(`/choreographies/${encodeURIComponent(props.doc.choreography_id)}`)
  let g = latest.graph
  if (needsLayout(g)) g = arrange(g)
  draft.reset({ graph: g, name: latest.name, description: latest.description ?? '', settings: latest.settings }, latest.version)
  emit('saved', latest)
}
async function discard() {
  if (!(await confirm({ title: 'Discard your changes?', text: 'The designer goes back to the saved version.', confirmText: 'Discard', danger: true }))) return
  const doc = props.doc
  let g: ChGraph = doc ? JSON.parse(JSON.stringify(doc.graph)) : { nodes: [], edges: [], triggers: [] }
  g.triggers ??= doc?.triggers ?? []
  if (g.nodes.length && needsLayout(g)) g = arrange(g)
  draft.reset({ graph: g, name: doc?.name ?? props.newName ?? '', description: doc?.description ?? '', settings: doc?.settings ?? { ...DEFAULT_SETTINGS } },
    doc?.latest_version ?? doc?.version ?? 0)
  restored.value = false
}

defineExpose({ dirty: draft.dirty, save })
</script>

<style scoped>
.axo-designer { display: flex; flex-direction: column; border-radius: 12px; border: 1px solid rgba(var(--v-border-color), 0.14); overflow: hidden;
  background: rgb(var(--v-theme-surface)); }
.axo-designer__bar { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; padding: 6px 10px; border-bottom: 1px solid rgba(var(--v-border-color), 0.12); min-height: 48px; }
.axo-designer__dirty { font-size: 0.78rem; color: rgb(var(--v-theme-muted)); margin-right: 6px; }
.axo-designer__note { font-size: 0.8rem; padding: 4px 12px; background: rgba(var(--v-theme-info), 0.07); display: flex; align-items: center; }
.axo-designer__main { display: flex; min-height: 0; }
.axo-designer__main :deep(.axo-canvas) { border: none; border-radius: 0; }
.axo-wrap :deep(.v-list-item-title) { white-space: normal; }
.axo-fade-enter-active, .axo-fade-leave-active { transition: opacity 0.25s ease; }
.axo-fade-enter-from, .axo-fade-leave-to { opacity: 0; }
</style>
