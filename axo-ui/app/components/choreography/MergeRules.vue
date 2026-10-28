<template>
  <div class="axo-merge">
    <div class="d-flex flex-wrap align-center ga-2">
      <span class="text-body-2">{{ lead }}</span>
      <v-select :model-value="rule.strategy" :items="items" density="compact" variant="outlined" hide-details :disabled="readonly"
                class="axo-merge__pick" label="Use" @update:model-value="setStrategy">
        <template #item="{ props: p, item }">
          <v-list-item v-bind="p" :subtitle="item.description" />
        </template>
      </v-select>
      <v-btn v-if="inherit && modelValue" size="small" variant="text" :disabled="readonly" @click="emit('update:modelValue', null)">Same as all</v-btn>
    </div>
    <div v-if="!sameEveryRun" class="text-caption mt-1" style="color: rgb(var(--v-theme-warning))">
      <v-icon icon="mdi-shuffle-variant" size="14" /> May change between runs.
    </div>

    <div v-if="rule.strategy === 'from'" class="mt-2">
      <v-select :model-value="rule.source" :items="senderItems" density="compact" variant="outlined" label="From step" hide-details
                :disabled="readonly" style="max-width: 280px" @update:model-value="set({ source: $event })" />
    </div>
    <div v-if="rule.strategy === 'reduce'" class="mt-2">
      <v-select :model-value="rule.reducer ?? 'sum'" :items="reducerItems" density="compact" variant="outlined" label="Combine by" hide-details
                :disabled="readonly" style="max-width: 280px" @update:model-value="set({ reducer: $event })" />
    </div>
    <div v-if="rule.strategy === 'collect'" class="mt-1">
      <v-switch :model-value="!!rule.as_list" color="primary" density="compact" hide-details inset :disabled="readonly"
                :label="rule.as_list ? 'As a list, in this order' : 'As {step: value}'" @update:model-value="set({ as_list: !!$event })" />
    </div>
    <div v-if="['priority', 'collect', 'reduce'].includes(rule.strategy)" class="mt-2">
      <div class="text-caption axo-muted mb-1">{{ rule.strategy === 'priority' ? 'The first step in this order that sends it wins' : 'Order' }} — drag to change</div>
      <TransitionGroup name="axo-order" tag="div" class="axo-merge__order">
        <div v-for="(s, i) in order" :key="s" class="axo-merge__chip" :draggable="!readonly" @dragstart="dragFrom = i"
             @dragover.prevent @drop.prevent="drop(i)">
          <v-icon icon="mdi-drag-vertical" size="16" class="axo-muted" />
          <span class="axo-merge__n">{{ i + 1 }}</span>{{ title(s) }}
          <v-btn v-if="!readonly && i > 0" icon="mdi-chevron-up" size="x-small" variant="text" :aria-label="`Move ${title(s)} up`" @click="move(i, i - 1)" />
        </div>
      </TransitionGroup>
    </div>
  </div>
</template>

<script setup lang="ts">
// How a parameter several earlier steps send is picked (the step's `merge`
// rules), in plain words: first in order (the default), only from one step,
// all of them, combine, first / last to finish, any of them, don't pass it.
import type { MergeStrategyInfo, ParamMerge } from '~/utils/choreographies'

const props = defineProps<{ modelValue: ParamMerge | null; senders: string[]; strategies: MergeStrategyInfo[]; lead: string
  /** A per-parameter rule: empty = the rule for every parameter. */
  inherit?: boolean; fallback?: ParamMerge | null; readonly?: boolean; title: (id: string) => string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: ParamMerge | null): void }>()

const WORDS: Record<string, string> = {
  priority: 'the first in order', from: 'only one step', collect: 'all of them', reduce: 'them combined',
  first_finished: 'the first to finish', last_finished: 'the last to finish', random: 'any of them', none: "none (don't pass it)",
}
const items = computed(() => props.strategies.map(s => ({ value: s.name, title: WORDS[s.name] ?? s.label, description: s.description })))
const rule = computed<ParamMerge>(() => props.modelValue ?? props.fallback ?? { strategy: 'priority' })
const info = computed(() => props.strategies.find(s => s.name === rule.value.strategy))
const sameEveryRun = computed(() => info.value?.same_every_run !== false)
const senderItems = computed(() => props.senders.map(s => ({ value: s, title: props.title(s) })))
const reducerItems = computed(() => (props.strategies.find(s => s.name === 'reduce')?.reducers ?? [])
  .map(r => ({ value: r.name, title: { sum: 'adding them', min: 'the smallest', max: 'the largest', concat: 'joining the lists', all_equal: 'all must be equal' }[r.name] ?? r.name, subtitle: r.description })))
const order = computed(() => {
  const set = (rule.value.order ?? []).filter(s => props.senders.includes(s))
  return [...set, ...props.senders.filter(s => !set.includes(s))]
})

function set(changes: Partial<ParamMerge>) {
  const next: ParamMerge = { ...rule.value, ...changes }
  emit('update:modelValue', clean(next))
}
function setStrategy(strategy: ParamMerge['strategy']) {
  const next: ParamMerge = { strategy }
  if (strategy === 'from') next.source = props.senders[0] ?? null
  if (strategy === 'reduce') next.reducer = 'sum'
  if (['priority', 'collect', 'reduce'].includes(strategy) && rule.value.order?.length) next.order = rule.value.order
  emit('update:modelValue', clean(next))
}
function clean(m: ParamMerge): ParamMerge {
  const out: ParamMerge = { strategy: m.strategy }
  if (m.order?.length && ['priority', 'collect', 'reduce'].includes(m.strategy)) out.order = m.order
  if (m.strategy === 'from') out.source = m.source ?? null
  if (m.strategy === 'reduce') out.reducer = m.reducer ?? 'sum'
  if (m.strategy === 'collect' && m.as_list) out.as_list = true
  return out
}
const dragFrom = ref<number | null>(null)
function move(from: number, to: number) {
  const list = [...order.value]
  const [x] = list.splice(from, 1)
  list.splice(to, 0, x!)
  set({ order: list })
}
function drop(to: number) {
  if (dragFrom.value !== null && dragFrom.value !== to) move(dragFrom.value, to)
  dragFrom.value = null
}
</script>

<style scoped>
.axo-merge__pick { max-width: 260px; flex: 1 1 200px; }
.axo-merge__order { display: flex; flex-direction: column; gap: 4px; max-width: 320px; }
.axo-merge__chip { display: flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: 8px; cursor: grab;
  border: 1px solid rgba(var(--v-border-color), 0.18); background: rgb(var(--v-theme-surface)); font-size: 0.85rem; }
.axo-merge__n { display: inline-grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; font-size: 0.7rem;
  background: rgba(var(--v-theme-primary), 0.12); color: rgb(var(--v-theme-primary)); }
.axo-order-move { transition: transform 0.2s ease; }
@media (prefers-reduced-motion: reduce) { .axo-order-move { transition: none; } }
</style>
