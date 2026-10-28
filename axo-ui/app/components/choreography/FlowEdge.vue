<template>
  <path :id="id" class="vue-flow__edge-path axo-edge" :class="[`e-${data.kind}`, data.state ? `st-${data.state}` : '', { 'is-flowing': data.flowing,
          'is-selected': selected, 'is-added': data.diff === 'added' }]" :d="path[0]" :marker-end="markerEnd" fill="none" />
  <path :d="path[0]" class="axo-edge__hit" fill="none" />
  <g v-if="pulse && motion" :key="pulse">
    <circle r="5.5" class="axo-edge__dot" :class="`e-${data.kind}`">
      <animateMotion :path="path[0]" dur="0.9s" fill="freeze" calcMode="spline" keyTimes="0;1" keySplines="0.4 0 0.2 1" />
      <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.08;0.85;1" dur="1.15s" fill="freeze" />
    </circle>
  </g>
  <EdgeLabelRenderer>
    <div v-if="data.label" class="axo-edge__label nodrag nopan" :class="[`e-${data.kind}`, data.state ? `st-${data.state}` : '']"
         :style="{ transform: `translate(-50%, -50%) translate(${path[1]}px, ${path[2]}px)` }" @click.stop="data.onLabel?.()">
      {{ data.label }}
    </div>
    <div v-if="carried" :key="`c${pulse}`" class="axo-edge__carried nodrag nopan"
         :style="{ transform: `translate(-50%, -140%) translate(${props.targetX - 40}px, ${props.targetY}px)` }">{{ carried }}</div>
  </EdgeLabelRenderer>
</template>

<script setup lang="ts">
// Vue Flow passes every edge field as attributes; the props below are the ones used
defineOptions({ inheritAttrs: false })
// An arrow: plain, with a condition (dashed, its rule as the label),
// "otherwise", "on failure" (red) or from a trigger (amber). While its step
// runs, a dash flows along it; when the step finishes and the arrow is
// followed, a glowing dot travels to the next step (with what it carried);
// an arrow not followed fades. No travelling for people who ask for less motion.
import { EdgeLabelRenderer, getBezierPath, type Position } from '@vue-flow/core'

export interface FlowEdgeData {
  kind: 'plain' | 'condition' | 'otherwise' | 'failure' | 'trigger'
  label?: string
  state?: 'taken' | 'not_taken' | string
  flowing?: boolean
  /** Goes up by one each time something travels along it (starts the dot). */
  pulse?: number
  carried?: string
  diff?: 'added' | null
  /** Clicking the label (HTML above the canvas, so Vue Flow's edge click misses it). */
  onLabel?: () => void
}
const props = defineProps<{
  id: string; sourceX: number; sourceY: number; targetX: number; targetY: number
  sourcePosition: Position; targetPosition: Position; data: FlowEdgeData; markerEnd?: string; selected?: boolean
}>()

const path = computed(() => getBezierPath({ sourceX: props.sourceX, sourceY: props.sourceY, sourcePosition: props.sourcePosition,
  targetX: props.targetX, targetY: props.targetY, targetPosition: props.targetPosition, curvature: 0.35 }))
const pulse = computed(() => props.data.pulse ?? 0)
const carried = computed(() => (pulse.value && props.data.carried ? props.data.carried : ''))
const motion = ref(true)
onMounted(() => { motion.value = !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches })
</script>

<style scoped>
.axo-edge { stroke: rgba(var(--v-border-color), 0.45); stroke-width: 2; transition: stroke 0.3s ease, opacity 0.4s ease; }
.axo-edge__hit { stroke: transparent; stroke-width: 18; }
.e-condition.axo-edge, .e-otherwise.axo-edge { stroke-dasharray: 7 5; }
.e-failure.axo-edge { stroke: rgba(var(--v-theme-error), 0.75); }
.e-trigger.axo-edge { stroke: rgba(var(--v-theme-warning), 0.8); stroke-dasharray: 2 5; stroke-linecap: round; }
.is-selected.axo-edge { stroke: rgb(var(--v-theme-primary)); stroke-width: 2.5; }
.is-added.axo-edge { stroke: rgb(var(--v-theme-success)); }
.st-taken.axo-edge { stroke: rgba(var(--v-theme-primary), 0.85); stroke-dasharray: none; }
.st-taken.e-failure.axo-edge { stroke: rgb(var(--v-theme-error)); }
.st-not_taken.axo-edge { opacity: 0.28; }
.is-flowing.axo-edge { stroke: rgba(var(--v-theme-info), 0.8); stroke-dasharray: 6 6; animation: axo-flow 0.7s linear infinite; }

.axo-edge__dot { fill: rgb(var(--v-theme-primary)); filter: drop-shadow(0 0 5px rgba(var(--v-theme-primary), 0.9)); }
.axo-edge__dot.e-failure { fill: rgb(var(--v-theme-error)); filter: drop-shadow(0 0 5px rgba(var(--v-theme-error), 0.9)); }
.axo-edge__dot.e-trigger { fill: rgb(var(--v-theme-warning)); filter: drop-shadow(0 0 5px rgba(var(--v-theme-warning), 0.9)); }

.axo-edge__label {
  position: absolute; pointer-events: all; cursor: pointer; font-size: 0.7rem; padding: 2px 7px; border-radius: 999px; max-width: 180px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; background: rgb(var(--v-theme-surface));
  border: 1px solid rgba(var(--v-border-color), 0.25); color: rgb(var(--v-theme-muted)); transition: opacity 0.4s ease;
}
.axo-edge__label.e-failure { color: rgb(var(--v-theme-error)); border-color: rgba(var(--v-theme-error), 0.4); }
.axo-edge__label.st-not_taken { opacity: 0.4; }
.axo-edge__carried {
  position: absolute; pointer-events: none; font-size: 0.68rem; padding: 2px 6px; border-radius: 6px; white-space: nowrap;
  background: rgba(var(--v-theme-primary), 0.12); color: rgb(var(--v-theme-primary)); font-family: ui-monospace, monospace;
  animation: axo-carried 2.2s ease forwards;
}
@keyframes axo-flow { to { stroke-dashoffset: -12; } }
@keyframes axo-carried { 0% { opacity: 0; } 30% { opacity: 1; } 80% { opacity: 1; } 100% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .is-flowing.axo-edge { animation: none; }
  .axo-edge__carried { animation: none; opacity: 1; }
}
</style>
