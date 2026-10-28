<template>
  <v-dialog :model-value="!!edge" max-width="720" scrollable @update:model-value="!$event && emit('close')">
    <v-card v-if="edge && draft">
      <v-card-title class="d-flex align-center">
        <span class="text-truncate">{{ title(edge.source_node_id) }} → {{ title(edge.target_node_id) }}</span>
        <v-spacer />
        <AxoHelp title="Arrows" text="An arrow passes the earlier step's answer to the next step. With a rule it's followed only when the answer matches; 'otherwise' is followed when none of that step's rules matched; 'on failure' only when the step fails." />
      </v-card-title>
      <v-card-text>
        <v-radio-group v-model="mode" :disabled="readonly" hide-details class="mb-3">
          <v-radio value="always" label="Always follow" />
          <v-radio value="when" label="Only when the answer matches rules" />
          <v-radio value="otherwise" label="Otherwise — when none of this step's rules matched" />
          <v-radio value="failure" :disabled="bucketEnd" :label="bucketEnd ? 'On failure (not from or into a bucket)' : 'On failure — when the step fails'" />
        </v-radio-group>

        <v-expand-transition>
          <div v-if="mode === 'when'">
            <ChoreographyConditionBuilder v-model="condition" :fields="fields" :operators="info?.condition_operators ?? []"
                                          field-label="Field of the answer" />
            <div class="axo-arrow__preview mt-3">
              <div class="text-caption axo-muted mb-1">Reads as</div>
              <div class="text-body-2">{{ conditionText(condition) || '—' }}</div>
              <template v-if="sample !== undefined">
                <div class="text-caption axo-muted mt-2 mb-1">With the last run's answer</div>
                <v-chip :color="preview ? 'success' : 'muted'" size="small" variant="tonal"
                        :prepend-icon="preview ? 'mdi-check' : 'mdi-close'">{{ preview ? 'It would be followed' : "It wouldn't be followed" }}</v-chip>
              </template>
            </div>
          </div>
        </v-expand-transition>

        <v-alert v-for="w in warnings" :key="w" type="warning" variant="tonal" density="compact" class="mt-3">{{ w }}</v-alert>
        <v-alert v-if="mode === 'failure'" type="info" variant="tonal" density="compact" class="mt-3">
          The next step gets the error (which step, the error's name and message, the job) instead of an answer, and the failure
          counts as handled: the run goes on and ends “completed with errors”.
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-btn v-if="!readonly" color="error" variant="text" prepend-icon="mdi-delete-outline" @click="emit('remove', edge.edge_id)">Remove arrow</v-btn>
        <v-spacer />
        <v-btn variant="text" @click="emit('close')">{{ readonly ? 'Close' : 'Cancel' }}</v-btn>
        <v-btn v-if="!readonly" color="primary" variant="flat" :disabled="mode === 'when' && !condition.rules.length" @click="done">Done</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { ChEdge, ChGraph, Condition, DesignerInfo } from '~/utils/choreographies'

const props = defineProps<{ edge: ChEdge | null; graph: ChGraph; info: DesignerInfo | null; readonly?: boolean
  /** The source step's answer in the last run (field suggestions, preview). */
  sample?: unknown; title: (nodeId: string) => string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'save', v: ChEdge): void; (e: 'remove', id: string): void }>()

const draft = ref<ChEdge | null>(null)
const mode = ref<'always' | 'when' | 'otherwise' | 'failure'>('always')
const condition = ref<Condition>({ match: 'all', rules: [] })
watch(() => props.edge, (e) => {
  draft.value = e ? JSON.parse(JSON.stringify(e)) : null
  if (!e) return
  mode.value = e.on_failure ? 'failure' : e.otherwise ? 'otherwise' : e.condition?.rules?.length ? 'when' : 'always'
  condition.value = e.condition?.rules?.length ? JSON.parse(JSON.stringify(e.condition)) : { match: 'all', rules: [{ field: '', op: '>', value: 0 }] }
}, { immediate: true })

const nodeKind = (id: string) => props.graph.nodes.find(n => n.node_id === id)?.kind
const bucketEnd = computed(() => !!props.edge && (nodeKind(props.edge.source_node_id) === 'bucket' || nodeKind(props.edge.target_node_id) === 'bucket'))
const fields = computed(() => (props.sample !== undefined ? fieldPaths(props.sample) : []))
const preview = computed(() => evaluate(condition.value, props.sample))

const warnings = computed(() => {
  if (!props.edge) return []
  const out: string[] = []
  const others = props.graph.edges.filter(e => e.source_node_id === props.edge!.source_node_id && e.edge_id !== props.edge!.edge_id && !e.on_failure)
  if (mode.value === 'otherwise' && others.some(e => e.otherwise)) out.push('This step already has an “otherwise” arrow: both would be followed together.')
  if (mode.value === 'otherwise' && !others.some(e => e.condition?.rules?.length)) out.push("This step has no arrow with rules, so “otherwise” is always followed.")
  if (mode.value === 'when' && !others.some(e => e.otherwise) && !others.some(e => e.condition?.rules?.length === 0 || !e.condition))
    out.push("When the rules don't match, nothing after this arrow runs (it shows as “not taken”). Add an “otherwise” arrow for that case.")
  if (mode.value === 'when' && props.sample !== undefined && props.sample !== null && typeof props.sample === 'object') {
    const known = new Set(fields.value)
    const walk = (c: Condition): string[] => c.rules.flatMap(r => (isGroup(r) ? walk(r) : r.field && !known.has(r.field) ? [r.field] : []))
    const missing = [...new Set(walk(condition.value))]
    if (missing.length) out.push(`The last run's answer has no ${missing.join(', ')}.`)
  }
  return out
})

function done() {
  if (!draft.value) return
  const e: ChEdge = { edge_id: draft.value.edge_id, source_node_id: draft.value.source_node_id, target_node_id: draft.value.target_node_id }
  if (mode.value === 'when') e.condition = condition.value
  if (mode.value === 'otherwise') e.otherwise = true
  if (mode.value === 'failure') e.on_failure = true
  emit('save', e)
}
</script>

<style scoped>
.axo-arrow__preview { padding: 10px 12px; border-radius: 8px; background: rgba(var(--v-border-color), 0.05); }
</style>
