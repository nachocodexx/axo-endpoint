<template>
  <div ref="wrap" class="axo-canvas" :class="{ 'is-full': full, 'is-editable': editable }" :style="{ height: full ? '100vh' : height }"
       @dragover.prevent="onDragOver" @drop.prevent="onDrop">
    <ClientOnly>
      <VueFlow :id="canvasId" :nodes="flowNodes" :edges="flowEdges" :nodes-draggable="editable" :nodes-connectable="editable"
               :elements-selectable="true" :delete-key-code="null" :zoom-on-double-click="false" :min-zoom="0.2" :max-zoom="1.8"
               :default-edge-options="{ type: 'flow' }" :connection-radius="28" class="axo-flow" @pane-ready="fit(false)"
               @node-click="onNodeClick" @edge-click="onEdgeClick" @pane-click="emit('pick', null)" @node-drag-stop="onDragStop"
               @connect="onConnect">
        <template #node-step="p"><ChoreographyStepNode v-bind="p" /></template>
        <template #node-trigger="p"><ChoreographyTriggerNode v-bind="p" /></template>
        <template #edge-flow="p"><ChoreographyFlowEdge v-bind="p" /></template>
        <Background :gap="18" :size="1.2" pattern-color="rgba(128,128,128,0.28)" />
        <MiniMap v-if="minimap && graph.nodes.length > 12" pannable zoomable class="axo-minimap d-none d-md-block"
                 :node-color="miniColor" mask-color="rgba(128,128,128,0.12)" />
      </VueFlow>
      <template #fallback><div class="axo-canvas__wait"><AxoLoader /></div></template>
    </ClientOnly>

    <div class="axo-canvas__tools">
      <slot name="tools" />
      <v-btn-group density="comfortable" variant="flat" class="axo-canvas__group" divided>
        <v-btn icon="mdi-minus" size="small" aria-label="Zoom out" @click="flow.zoomOut()" />
        <v-btn icon="mdi-fit-to-screen-outline" size="small" aria-label="Fit to the screen" @click="fit()" />
        <v-btn icon="mdi-plus" size="small" aria-label="Zoom in" @click="flow.zoomIn()" />
        <v-btn :icon="full ? 'mdi-fullscreen-exit' : 'mdi-fullscreen'" size="small" :aria-label="full ? 'Leave full screen (Esc)' : 'Full screen (F)'"
               :title="full ? 'Leave full screen (Esc)' : 'Full screen (F)'" @click="toggleFull" />
      </v-btn-group>
    </div>
    <AxoEmptyState v-if="!graph.nodes.length && emptyText" icon="mdi-sitemap-outline" :title="emptyTitle" :text="emptyText" class="axo-canvas__empty" />
    <slot />
  </div>
</template>

<script setup lang="ts">
// The canvas every choreography page draws on (Vue Flow): the overview and
// versions (read only), the designer (drag, connect, drop from the side
// panel) and runs (each step's state, arrows followed or not, a dot
// travelling when a step's answer moves on). Full screen with the button or
// F (Esc leaves). `selected` = a step id, "edge:<id>" or a trigger node id.
import { MarkerType, VueFlow, useVueFlow, type Connection, type Edge, type Node } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/minimap/dist/style.css'
import type { ChGraph, GraphDiff, Problem, Run } from '~/utils/choreographies'
import type { StepNodeData } from './StepNode.vue'
import type { FlowEdgeData } from './FlowEdge.vue'
import type { TriggerNodeData } from './TriggerNode.vue'

export type CanvasPick = { kind: 'node' | 'edge' | 'trigger'; id: string } | null

const props = withDefaults(defineProps<{
  graph: ChGraph
  canvasId: string
  editable?: boolean
  fname?: (id: string) => string
  run?: Run | null
  live?: boolean
  now?: number
  problems?: Problem[]
  diff?: GraphDiff | null
  selected?: string | null
  height?: string
  minimap?: boolean
  carried?: Record<string, string>
  /** The step whose answer is the run's answer (gets an "Answer" badge). */
  answerStep?: string | null
  emptyTitle?: string
  emptyText?: string
}>(), { height: '520px', minimap: true, fname: (id: string) => id, emptyTitle: 'No steps yet' })
const emit = defineEmits<{
  (e: 'pick', v: CanvasPick): void
  (e: 'move', nodeId: string, pos: { x: number; y: number }): void
  (e: 'connect', source: string, target: string): void
  (e: 'drop', payload: string, pos: { x: number; y: number }): void
}>()

const flow = useVueFlow(props.canvasId)
const { byId: chById } = useChoreographies()
const chInfo = (id: string) => chById(id)
const wrap = ref<HTMLElement | null>(null)

const byNode = computed(() => {
  const out: Record<string, string[]> = {}
  for (const p of props.problems ?? []) {
    const key = p.node_id ?? (p.edge_id ? `edge:${p.edge_id}` : p.trigger_id ? `trigger:${p.trigger_id}` : '')
    if (key) (out[key] ??= []).push(p.message)
  }
  return out
})

const flowNodes = computed<Node[]>(() => {
  const g = props.graph
  const steps: Node[] = g.nodes.map((n) => {
    const data: StepNodeData = {
      node: n, title: nodeTitle(n, props.fname), subtitle: nodeSubtitle(n, props.fname, undefined, chInfo), answer: props.answerStep === n.node_id,
      role: n.kind === 'bucket' ? bucketRole(g, n.node_id) : undefined,
      state: props.run?.steps?.[n.node_id] ?? null, runStatus: props.run?.status ?? null,
      problems: byNode.value[n.node_id], live: props.live, now: props.now, maxAttempts: n.max_attempts,
      diff: props.diff?.added.includes(n.node_id) ? 'added' : props.diff?.changed.includes(n.node_id) ? 'changed' : null,
    }
    return { id: n.node_id, type: 'step', position: { x: n.position?.x ?? 0, y: n.position?.y ?? 0 }, data,
      selected: props.selected === n.node_id, draggable: props.editable, connectable: props.editable }
  })
  const places = triggerPositions(g)
  const triggers: Node[] = (g.triggers ?? []).map((t, i) => {
    const id = TRIGGER_NODE(t, i)
    const file = t.event_type === 'file.written'
    const fired = !!props.run?.trigger && (props.run.trigger.trigger_id === t.trigger_id)
    const data: TriggerNodeData = {
      index: i, icon: file ? 'mdi-file-upload-outline' : 'mdi-lightning-bolt',
      title: file ? `New file${t.bucket ? ` in ${t.bucket}` : ''}${t.prefix ? `/${t.prefix}` : ''}` : t.event_type,
      text: [file ? 'A new file starts a run' : `An event ${t.event_type} starts a run`, conditionText(t.condition)].filter(Boolean).join(' · when '),
      fired, problems: byNode.value[`trigger:${t.trigger_id}`],
    }
    return { id, type: 'trigger', position: places[id] ?? { x: -140, y: i * 90 }, data, draggable: false,
      connectable: false, selected: props.selected === id }
  })
  return [...triggers, ...steps]
})

// Something travelled: count up per arrow, each one starts its dot.
const pulses = ref<Record<string, number>>({})
watch(() => props.run && { states: { ...props.run.edge_states }, id: props.run.run_id }, (now, before) => {
  if (!now || !props.live) return
  const old = before && before.id === now.id ? before.states : {}
  const next = { ...pulses.value }
  for (const [edge, st] of Object.entries(now.states)) if (st === 'taken' && old[edge] !== 'taken') next[edge] = (next[edge] ?? 0) + 1
  pulses.value = next
}, { deep: true })
watch(() => props.run?.status, (now, before) => {
  if (props.live && before === 'queued' && now === 'running' && props.run?.trigger) {
    const next = { ...pulses.value }
    ;(props.graph.triggers ?? []).forEach((t, i) => { if (t.trigger_id === props.run?.trigger?.trigger_id) next[`t${i}`] = (next[`t${i}`] ?? 0) + 1 })
    pulses.value = next
  }
})

const COLORS = { plain: 'rgba(128,128,128,0.6)', condition: 'rgba(128,128,128,0.6)', otherwise: 'rgba(128,128,128,0.6)', failure: '#e5484d', trigger: '#f5a524' }
const flowEdges = computed<Edge[]>(() => {
  const g = props.graph
  const run = props.run
  const edges: Edge[] = g.edges.map((e) => {
    const kind: FlowEdgeData['kind'] = e.on_failure ? 'failure' : e.otherwise ? 'otherwise' : e.condition ? 'condition' : 'plain'
    const st = run?.edge_states?.[e.edge_id]
    const source = run?.steps?.[e.source_node_id]
    const data: FlowEdgeData = {
      kind, label: edgeText(e) || undefined, state: st, flowing: source?.status === 'running' && !e.on_failure,
      pulse: pulses.value[e.edge_id] ?? 0, carried: props.carried?.[e.edge_id],
      diff: props.diff?.edgesAdded.includes(`${e.source_node_id}→${e.target_node_id}`) ? 'added' : null,
      onLabel: () => emit('pick', { kind: 'edge', id: e.edge_id }),
    }
    return { id: e.edge_id, source: e.source_node_id, target: e.target_node_id, type: 'flow', data,
      selected: props.selected === `edge:${e.edge_id}`,
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: st === 'not_taken' ? 'rgba(128,128,128,0.3)' : COLORS[kind] } }
  })
  ;(g.triggers ?? []).forEach((t, i) => {
    for (const target of startTargets(g, t)) {
      edges.push({ id: `trig:${i}:${target}`, source: TRIGGER_NODE(t, i), target, type: 'flow',
        data: { kind: 'trigger', pulse: pulses.value[`t${i}`] ?? 0 } as FlowEdgeData,
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: COLORS.trigger } })
    }
  })
  return edges
})

function onNodeClick({ node }: { node: Node }) {
  emit('pick', node.type === 'trigger' ? { kind: 'trigger', id: node.id } : { kind: 'node', id: node.id })
}
function onEdgeClick({ edge }: { edge: Edge }) {
  if (edge.id.startsWith('trig:')) emit('pick', { kind: 'trigger', id: edge.source })
  else emit('pick', { kind: 'edge', id: edge.id })
}
function onDragStop({ node }: { node: Node }) {
  if (node.type === 'step') emit('move', node.id, { x: Math.round(node.position.x), y: Math.round(node.position.y) })
}
function onConnect(c: Connection) {
  if (c.source && c.target && c.source !== c.target && !c.source.startsWith('trigger:')) emit('connect', c.source, c.target)
}
function onDragOver(ev: DragEvent) {
  if (props.editable && ev.dataTransfer) ev.dataTransfer.dropEffect = 'copy'
}
function onDrop(ev: DragEvent) {
  const payload = ev.dataTransfer?.getData('application/x-axo-step')
  if (!props.editable || !payload) return
  const p = flow.screenToFlowCoordinate({ x: ev.clientX, y: ev.clientY })
  emit('drop', payload, { x: Math.round(p.x - NODE_W / 2), y: Math.round(p.y - NODE_H / 2) })
}

function miniColor(n: Node) {
  const st = (n.data as StepNodeData | undefined)?.state?.status
  return st === 'failed' ? '#e5484d' : st === 'completed' ? '#30a46c' : st === 'running' ? '#0086ff' : n.type === 'trigger' ? '#f5a524' : '#9aa4b2'
}
/** The whole graph in view -- except on a phone, where it would be
 *  too small to read: there it keeps a readable size, starting at the left
 *  (the first steps), and the rest is a pan away. */
const READABLE_ZOOM = 0.6
async function fit(animate = true) {
  await flow.fitView({ padding: 0.18, duration: animate ? 300 : 0 })
  const v = flow.getViewport()
  const drawn = flow.getNodes.value          // steps and trigger cards
  if (v.zoom >= READABLE_ZOOM || !drawn.length || window.innerWidth >= 600) return   // phones only
  const xs = drawn.map(n => n.position.x)
  const ys = drawn.map(n => n.position.y)
  const midY = (Math.min(...ys) + Math.max(...ys) + NODE_H) / 2
  const h = flow.dimensions.value.height
  flow.setViewport({ x: 16 - Math.min(...xs) * READABLE_ZOOM, y: h / 2 - midY * READABLE_ZOOM, zoom: READABLE_ZOOM }, { duration: animate ? 300 : 0 })
}
/** Where a new step goes: right of `afterId`, or right of everything. */
function placeAfter(afterId?: string | null): { x: number; y: number } {
  const g = props.graph
  const after = afterId ? g.nodes.find(n => n.node_id === afterId) : null
  if (after) return { x: (after.position?.x ?? 0) + NODE_W + 90, y: after.position?.y ?? 0 }
  const right = Math.max(-NODE_W - 90, ...g.nodes.map(n => n.position?.x ?? 0))
  return { x: right + NODE_W + 90, y: g.nodes.length ? Math.min(...g.nodes.map(n => n.position?.y ?? 0)) : 0 }
}
function focus(nodeId: string) {
  const n = props.graph.nodes.find(x => x.node_id === nodeId)
  if (n) flow.setCenter((n.position?.x ?? 0) + NODE_W / 2, (n.position?.y ?? 0) + NODE_H / 2, { zoom: Math.max(flow.getViewport().zoom, 0.9), duration: 350 })
}

// full screen: the button, F (not while typing), Esc
const full = ref(false)
async function toggleFull() {
  if (!wrap.value) return
  try {
    if (!document.fullscreenElement) await wrap.value.requestFullscreen()
    else await document.exitFullscreen()
  } catch { full.value = !full.value }        // no Fullscreen API: fill the window instead
}
function onFullChange() { full.value = document.fullscreenElement === wrap.value; nextTick(fit) }
function onKey(ev: KeyboardEvent) {
  const t = ev.target as HTMLElement | null
  if (t && (t.closest('input, textarea, [contenteditable="true"], .v-overlay') || ev.metaKey || ev.ctrlKey || ev.altKey)) return
  if ((ev.key === 'f' || ev.key === 'F') && wrap.value?.matches(':hover, :focus-within')) { ev.preventDefault(); toggleFull() }
  if (ev.key === 'Escape' && full.value && !document.fullscreenElement) full.value = false
}
onMounted(() => { document.addEventListener('fullscreenchange', onFullChange); window.addEventListener('keydown', onKey) })
onBeforeUnmount(() => { document.removeEventListener('fullscreenchange', onFullChange); window.removeEventListener('keydown', onKey) })
// a graph that arrives later (or gets its steps) is fitted once
watch(() => props.graph.nodes.length, (n, before) => { if (n && !before) setTimeout(fit, 60) })

defineExpose({ fit, focus, placeAfter, toggleFull, full })
</script>

<style scoped>
.axo-canvas { position: relative; border-radius: 12px; overflow: hidden; border: 1px solid rgba(var(--v-border-color), 0.14);
  background: rgba(var(--v-border-color), 0.025); }
.axo-canvas.is-full { position: fixed; inset: 0; z-index: 2000; border-radius: 0; background: rgb(var(--v-theme-background)); }
:fullscreen.axo-canvas { background: rgb(var(--v-theme-background)); }
.axo-flow { width: 100%; height: 100%; }
.axo-canvas__wait { height: 100%; display: grid; place-items: center; }
.axo-canvas__tools { position: absolute; right: 12px; bottom: 12px; display: flex; gap: 8px; align-items: center; z-index: 5; }
.axo-canvas__group { border: 1px solid rgba(var(--v-border-color), 0.16); box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08); }
.axo-canvas__empty { position: absolute; inset: 0; display: grid; place-content: center; pointer-events: none; }
.axo-minimap { right: 12px; bottom: 56px; border-radius: 8px; overflow: hidden; background: rgb(var(--v-theme-surface)) !important;
  border: 1px solid rgba(var(--v-border-color), 0.16); }
:deep(.vue-flow__edge.selected .vue-flow__edge-path) { stroke: rgb(var(--v-theme-primary)); }
:deep(.vue-flow__node) { border-radius: 12px; }
:deep(.vue-flow__node:focus-visible) { outline: 2px solid rgb(var(--v-theme-primary)); outline-offset: 2px; }
:deep(.vue-flow__connection-path) { stroke: rgb(var(--v-theme-primary)); stroke-width: 2; stroke-dasharray: 5 4; }
</style>
