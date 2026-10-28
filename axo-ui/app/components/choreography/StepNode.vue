<template>
  <div class="axo-step" :class="[`k-${data.node.kind}`, `s-${status}`, { 'is-selected': selected, 'is-pop': pop, 'is-shake': shake,
         'is-added': data.diff === 'added', 'is-changed': data.diff === 'changed', 'is-problem': data.problems?.length }]"
       :title="data.problems?.join('\n') || undefined">
    <Handle v-if="data.node.kind !== 'bucket' || data.role !== 'source'" type="target" :position="Position.Left" class="axo-handle" />
    <div class="axo-step__body">
      <div class="axo-step__icon" :style="{ color: `rgb(var(--v-theme-${kind.color}))` }">
        <v-icon :icon="kind.icon" size="18" />
      </div>
      <div class="axo-step__text">
        <div class="axo-step__title text-truncate">{{ data.title }}</div>
        <div class="axo-step__sub text-truncate">{{ data.subtitle }}</div>
      </div>
      <div class="axo-step__state">
        <svg v-if="files" class="axo-step__files" viewBox="0 0 36 36" :aria-label="`${files.done} of ${files.total} files`">
          <circle cx="18" cy="18" r="15" class="track" />
          <circle cx="18" cy="18" r="15" class="fill" :style="{ strokeDasharray: `${files.pct * 94.2} 94.2` }" />
        </svg>
        <AxoRunningRing v-else-if="status === 'running'" :size="16" />
        <v-icon v-else-if="state && status !== 'pending'" :icon="look.icon" size="18" :color="look.color" />
        <v-icon v-else-if="data.problems?.length" icon="mdi-alert" size="18" color="warning" />
      </div>
    </div>
    <div v-if="badges.length" class="axo-step__badges">
      <span v-for="b in badges" :key="b.text" class="axo-step__badge" :class="b.cls">{{ b.text }}</span>
    </div>
    <Handle type="source" :position="Position.Right" class="axo-handle" />
  </div>
</template>

<script setup lang="ts">
// One step on the canvas: what it runs, and during / after a run its state:
// waiting (dim), queued (soft pulse), running (light going round the border,
// the running ring, "running · 12 s"), per-file progress ("3 / 8 files"), a retry ("try 2 of 3",
// amber), done (a short pop), failed (a short shake), not taken (faded).
// A choreography step looks like a small stack of cards and shows its own
// run's progress ("2 of 3 steps", "3rd in line").
// Animations only when the state changes while watching (`data.live`), and
// none for people who ask for less motion (see the styles).
import { Handle, Position } from '@vue-flow/core'
import type { BucketRole, ChNode, StepState } from '~/utils/choreographies'

export interface StepNodeData {
  node: ChNode
  title: string
  subtitle: string
  role?: BucketRole
  state?: StepState | null
  runStatus?: string | null
  problems?: string[]
  diff?: 'added' | 'changed' | null
  live?: boolean
  now?: number
  maxAttempts?: number | null
  /** Its answer is the choreography's answer (when another one calls it). */
  answer?: boolean
}
const props = defineProps<{ id: string; data: StepNodeData; selected?: boolean }>()

const kind = computed(() => KIND_INFO[props.data.node.kind])
const state = computed(() => props.data.state ?? null)
const status = computed(() => {
  const s = state.value?.status ?? (props.data.runStatus ? 'pending' : 'idle')
  return s === 'pending' && props.data.runStatus === 'queued' ? 'queued' : s
})
const look = computed(() => stepLook(status.value))

const files = computed(() => {
  const items = state.value?.items ?? []
  if (items.length < 2) return null
  const done = items.filter(i => !['pending', 'running'].includes(i.status)).length
  return { done, total: items.length, pct: done / items.length }
})

const badges = computed(() => {
  const out: Array<{ text: string; cls?: string }> = []
  const s = state.value
  if (props.data.answer) out.push({ text: 'Answer', cls: 'ans' })
  if (props.data.node.kind === 'bucket' && props.data.role) out.push({ text: BUCKET_ROLE_TEXT[props.data.role] })
  if (s?.child && ['pending', 'running'].includes(s.status)) out.push({ text: childText(s.child) })
  if (files.value) out.push({ text: `${files.value.done} / ${files.value.total} files` })
  if (s && s.attempt > 1 && ['running', 'pending'].includes(s.status)) {
    out.push({ text: `try ${s.attempt}${props.data.maxAttempts ? ` of ${props.data.maxAttempts}` : ''}`, cls: 'warn' })
  }
  if (s?.status === 'running' && s.started_at && props.data.now) {
    out.push({ text: `running · ${formatDuration(Math.max(0, props.data.now - Date.parse(s.started_at)))}`, cls: 'run' })
  } else if (s?.duration_ms != null && !['pending', 'running'].includes(s.status)) {
    out.push({ text: formatMs(s.duration_ms) })
  }
  if (s && ['failed', 'completed_with_errors'].includes(s.status) && s.error_name) out.push({ text: s.error_name, cls: 'err' })
  return out
})

// a short pop / shake when it finishes while someone watches
const pop = ref(false)
const shake = ref(false)
watch(() => state.value?.status, (now, before) => {
  if (!props.data.live || !before || now === before) return
  if (now === 'completed' || now === 'completed_with_errors') { pop.value = true; setTimeout(() => { pop.value = false }, 650) }
  if (now === 'failed') { shake.value = true; setTimeout(() => { shake.value = false }, 650) }
})
</script>

<style scoped>
.axo-step {
  position: relative; width: 220px; min-height: 76px; padding: 10px 12px 8px; border-radius: 12px;
  background: rgb(var(--v-theme-surface)); border: 1px solid rgba(var(--v-border-color), 0.22);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06), 0 4px 14px rgba(0, 0, 0, 0.05);
  transition: opacity 0.4s ease, box-shadow 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}
.axo-step::after { content: ''; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px; border-radius: 0 3px 3px 0; background: var(--accent); }
.k-function { --accent: rgb(var(--v-theme-primary)); }
.k-object { --accent: rgb(var(--v-theme-info)); }
/* a choreography step: two cards peeking out behind it */
.k-choreography { --accent: rgb(var(--v-theme-success));
  --stack: 4px 4px 0 -1px rgb(var(--v-theme-surface)), 4px 4px 0 0 rgba(var(--v-border-color), 0.24),
    8px 8px 0 -1px rgb(var(--v-theme-surface)), 8px 8px 0 0 rgba(var(--v-border-color), 0.16);
  background: linear-gradient(180deg, rgba(var(--v-theme-success), 0.06), rgb(var(--v-theme-surface)) 60%), rgb(var(--v-theme-surface));
  box-shadow: var(--stack), 0 4px 14px rgba(0, 0, 0, 0.05); }
.k-bucket { --accent: rgb(var(--v-theme-brand-3)); border-style: solid; background: linear-gradient(180deg, rgba(var(--v-theme-brand-3), 0.06), rgb(var(--v-theme-surface)) 60%); }
.axo-step:hover { box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08), 0 8px 24px rgba(0, 0, 0, 0.08); }
.is-selected { border-color: rgb(var(--v-theme-primary)); box-shadow: 0 0 0 3px rgba(var(--v-theme-primary), 0.18); }
.is-problem { border-color: rgba(var(--v-theme-warning), 0.7); }
.is-added { box-shadow: 0 0 0 3px rgba(var(--v-theme-success), 0.35); }
.is-changed { box-shadow: 0 0 0 3px rgba(var(--v-theme-warning), 0.35); }
.k-choreography:hover { box-shadow: var(--stack), 0 8px 24px rgba(0, 0, 0, 0.08); }
.k-choreography.is-selected { box-shadow: 0 0 0 3px rgba(var(--v-theme-primary), 0.18), var(--stack); }
.k-choreography.is-added { box-shadow: 0 0 0 3px rgba(var(--v-theme-success), 0.35), var(--stack); }
.k-choreography.is-changed { box-shadow: 0 0 0 3px rgba(var(--v-theme-warning), 0.35), var(--stack); }

.axo-step__body { display: flex; align-items: center; gap: 10px; }
.axo-step__icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 9px; flex: 0 0 auto;
  background: rgba(var(--v-border-color), 0.07); }
.axo-step__text { flex: 1 1 auto; min-width: 0; }
.axo-step__title { font-size: 0.88rem; font-weight: 600; line-height: 1.2; }
.axo-step__sub { font-size: 0.74rem; color: rgb(var(--v-theme-muted)); line-height: 1.3; }
.axo-step__state { width: 22px; display: grid; place-items: center; flex: 0 0 auto; }
.axo-step__files { width: 22px; height: 22px; transform: rotate(-90deg); }
.axo-step__files circle { fill: none; stroke-width: 4; }
.axo-step__files .track { stroke: rgba(var(--v-border-color), 0.18); }
.axo-step__files .fill { stroke: rgb(var(--v-theme-info)); transition: stroke-dasharray 0.5s ease; }
.axo-step__badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 7px; }
.axo-step__badge { font-size: 0.68rem; line-height: 1; padding: 3px 6px; border-radius: 6px; background: rgba(var(--v-border-color), 0.08);
  color: rgb(var(--v-theme-muted)); white-space: nowrap; }
.axo-step__badge.run { background: rgba(var(--v-theme-info), 0.12); color: rgb(var(--v-theme-info)); font-variant-numeric: tabular-nums; }
.axo-step__badge.ans { background: rgba(var(--v-theme-success), 0.14); color: rgb(var(--v-theme-success)); font-weight: 600; }
.axo-step__badge.warn { background: rgba(var(--v-theme-warning), 0.16); color: rgb(var(--v-theme-warning)); }
.axo-step__badge.err { background: rgba(var(--v-theme-error), 0.12); color: rgb(var(--v-theme-error)); font-family: ui-monospace, monospace; }

/* states */
.s-pending { opacity: 0.62; }
.s-queued { animation: axo-breathe 1.8s ease-in-out infinite; }
.s-running { border-color: transparent; }
.s-running::before {
  content: ''; position: absolute; inset: -2px; border-radius: 14px; padding: 2px; pointer-events: none;
  background: conic-gradient(from var(--axo-angle), rgba(var(--v-theme-info), 0) 0 55%, rgba(var(--v-theme-info), 0.9) 85%, rgba(var(--v-theme-info), 0) 100%);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude;
  animation: axo-turn 2s linear infinite;
}
.s-completed { border-color: rgba(var(--v-theme-success), 0.55); }
.s-completed_with_errors { border-color: rgba(var(--v-theme-warning), 0.7); }
.s-failed { border-color: rgba(var(--v-theme-error), 0.75); background: linear-gradient(180deg, rgba(var(--v-theme-error), 0.06), rgb(var(--v-theme-surface)) 70%); }
.s-not_taken { opacity: 0.42; border-style: dashed; box-shadow: none; }
.s-skipped, .s-cancelled { opacity: 0.55; border-style: dashed; }
.is-pop { animation: axo-pop 0.6s cubic-bezier(0.2, 1.6, 0.4, 1); }
.is-shake { animation: axo-shake 0.55s ease; }

.axo-handle { width: 9px; height: 9px; border: 2px solid rgb(var(--v-theme-surface)); background: rgba(var(--v-border-color), 0.5); }
.axo-handle:hover { background: rgb(var(--v-theme-primary)); }

@property --axo-angle { syntax: '<angle>'; inherits: false; initial-value: 0deg; }
@keyframes axo-turn { to { --axo-angle: 360deg; } }
@keyframes axo-breathe { 0%, 100% { box-shadow: 0 0 0 0 rgba(var(--v-theme-info), 0.0); } 50% { box-shadow: 0 0 0 6px rgba(var(--v-theme-info), 0.16); } }
@keyframes axo-pop { 0% { transform: scale(1); } 40% { transform: scale(1.06); } 100% { transform: scale(1); } }
@keyframes axo-shake { 0%, 100% { transform: translateX(0); } 20% { transform: translateX(-5px); } 40% { transform: translateX(5px); } 60% { transform: translateX(-3px); } 80% { transform: translateX(3px); } }
@media (prefers-reduced-motion: reduce) {
  .s-queued, .s-running::before, .is-pop, .is-shake { animation: none; }
  .s-running { border-color: rgb(var(--v-theme-info)); }
}
</style>
