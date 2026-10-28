<template>
  <v-dialog :model-value="!!node" max-width="760" scrollable @update:model-value="!$event && emit('close')">
    <v-card v-if="node && d">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon :icon="KIND_INFO[d.kind].icon" :color="KIND_INFO[d.kind].color" />
        <span class="text-truncate">{{ nodeTitle(d, fname) }}</span>
        <v-chip v-if="d.kind === 'bucket'" size="small" variant="tonal">{{ BUCKET_ROLE_TEXT[role] }}</v-chip>
        <v-spacer />
        <AxoHelp title="A step" text="A step runs a function, an object's method, a whole other choreography (as its own run, linked to this one), or reads / saves files in a bucket. Its parameters come, in order, from values set here, the bucket file before it, the answers of the steps before it (matched by name), and finally the code's own default." />
      </v-card-title>
      <v-card-text class="pt-2">
        <v-text-field v-model="d.label" density="compact" variant="outlined" label="Name on the canvas" placeholder="optional"
                      :disabled="readonly" hide-details="auto" class="mb-3" />
        <v-expansion-panels v-model="open" multiple variant="accordion">
          <!-- what it runs -->
          <v-expansion-panel value="what">
            <v-expansion-panel-title>
              <div><div class="font-weight-medium">What it runs</div><div class="text-caption axo-muted">{{ nodeSubtitle(d, fname, graph, chById) }}</div></div>
            </v-expansion-panel-title>
            <v-expansion-panel-text>
              <template v-if="d.kind === 'function'">
                <div class="d-flex flex-wrap ga-3">
                  <v-autocomplete :model-value="d.function_id" :items="functionItems" density="compact" variant="outlined" label="Function"
                                  :disabled="readonly" hide-details="auto" style="flex: 2 1 240px" @update:model-value="pickFunction" />
                  <v-select :model-value="d.function_version ?? null" :items="versionItems" density="compact" variant="outlined" label="Version"
                            :disabled="readonly || !d.function_id" hide-details style="flex: 1 1 140px" @update:model-value="d.function_version = $event" />
                </div>
              </template>
              <template v-else-if="d.kind === 'object'">
                <div class="d-flex flex-wrap ga-3">
                  <v-autocomplete :model-value="d.object_name" :items="objects.map(o => o.name)" density="compact" variant="outlined" label="Object"
                                  :disabled="readonly" hide-details style="flex: 2 1 220px" @update:model-value="pickObject" />
                  <v-select :model-value="d.method || 'run'" :items="methodItems" density="compact" variant="outlined" label="Method"
                            :disabled="readonly || !d.object_name" hide-details style="flex: 1 1 160px" @update:model-value="d.method = $event === 'run' ? null : $event" />
                </div>
                <div class="text-caption axo-muted mt-2">Calls on one object run one at a time, in order.</div>
              </template>
              <template v-else-if="d.kind === 'choreography'">
                <div class="d-flex flex-wrap ga-3">
                  <v-autocomplete :model-value="d.choreography_id" :items="chItems" density="compact" variant="outlined" label="Choreography"
                                  :disabled="readonly" hide-details="auto" style="flex: 2 1 240px" @update:model-value="pickChoreography" />
                  <v-select :model-value="d.choreography_version ?? null" :items="chVersionItems" density="compact" variant="outlined" label="Version"
                            :disabled="readonly || !d.choreography_id" hide-details style="flex: 1 1 160px" @update:model-value="d.choreography_version = $event" />
                </div>
                <div class="text-caption axo-muted mt-2">
                  It runs as its own run, linked to this one. The version is fixed when this run starts; a resume uses the same one.
                </div>
                <div v-if="inner" class="mt-3">
                  <div class="d-flex align-center mb-1">
                    <span class="text-body-2 font-weight-medium">Its steps (v{{ inner.version }})</span><v-spacer />
                    <v-btn size="small" variant="text" append-icon="mdi-open-in-new" :to="`/ves/${veId}/choreographies/${encodeURIComponent(inner.name)}`" target="_blank">Open</v-btn>
                  </div>
                  <ChoreographyCanvas :graph="innerGraph!" :canvas-id="`ch-preview-${node.node_id}`" :fname="fname" height="220px" :minimap="false"
                                      :answer-step="inner.settings.answer_step ?? null" />
                </div>
                <AxoSkeleton v-else-if="d.choreography_id && loadingInner" :lines="3" class="mt-3" />
              </template>
              <template v-else>
                <v-autocomplete v-model="d.bucket_name" :items="buckets" density="compact" variant="outlined" label="Bucket" :disabled="readonly"
                                hide-details="auto" class="mb-2" />
                <div class="text-body-2 axo-muted">{{ ROLE_HELP[role] }}</div>
              </template>
            </v-expansion-panel-text>
          </v-expansion-panel>

          <!-- parameters -->
          <v-expansion-panel v-if="d.kind !== 'bucket'" value="params">
            <v-expansion-panel-title>
              <div><div class="font-weight-medium">Parameters</div>
                <div class="text-caption" :class="missing.length ? 'text-error' : 'axo-muted'">
                  {{ missing.length ? `Missing: ${missing.join(', ')}` : `${params.length} parameter${params.length === 1 ? '' : 's'}` }}
                </div>
              </div>
            </v-expansion-panel-title>
            <v-expansion-panel-text>
              <div v-if="senders.length > 1" class="axo-step-dlg__all mb-4">
                <ChoreographyMergeRules :model-value="d.merge?.['*'] ?? null" :senders="senders" :strategies="info?.merge_strategies ?? []"
                                        :readonly="readonly" :title="title" :lead="`For every parameter ${senders.length} steps send, use`"
                                        @update:model-value="setMerge('*', $event)" />
              </div>
              <div v-if="!params.length" class="text-body-2 axo-muted">
                {{ known ? 'It takes no parameters.' : d.kind === 'choreography' ? "Its inputs aren't known (a first step has no type hints): every field of the earlier answers is passed."
                  : "Its parameters aren't known (no type hints): every field of the earlier answers is passed." }}
              </div>
              <div v-for="p in params" :key="p.name" class="axo-step-dlg__param">
                <div class="d-flex align-center flex-wrap ga-2 mb-1">
                  <span class="axo-mono font-weight-medium">{{ p.name }}</span>
                  <v-chip size="x-small" variant="tonal" :color="originOf(p).color">{{ originOf(p).text }}</v-chip>
                  <v-spacer />
                  <v-btn v-if="!readonly && isSet(p.name)" size="x-small" variant="text" @click="unset(p.name)">Don't set here</v-btn>
                  <v-btn v-else-if="!readonly" size="x-small" variant="text" prepend-icon="mdi-pencil" @click="setHere(p)">Set here</v-btn>
                </div>
                <ParamField v-if="isSet(p.name)" :spec="specOf(p)" :model-value="d.params?.[p.name]" :disabled="readonly"
                            @update:model-value="setParam(p.name, $event)" />
                <div v-if="lastRun?.params && p.name in lastRun.params" class="text-caption axo-muted mt-1">
                  Last run: <span class="axo-mono">{{ short(lastRun.params[p.name]) }}</span> · {{ originText(lastRun.param_origins?.[p.name], title) }}
                </div>
                <div v-if="senders.length > 1 && !isSet(p.name)" class="mt-2">
                  <ChoreographyMergeRules :model-value="d.merge?.[p.name] ?? null" :fallback="d.merge?.['*'] ?? null" inherit :senders="senders"
                                          :strategies="info?.merge_strategies ?? []" :readonly="readonly" :title="title"
                                          :lead="`When ${senders.length} steps send ${p.name}, use`" @update:model-value="setMerge(p.name, $event)" />
                </div>
              </div>
            </v-expansion-panel-text>
          </v-expansion-panel>

          <!-- running -->
          <v-expansion-panel v-if="d.kind !== 'bucket'" value="running">
            <v-expansion-panel-title>
              <div><div class="font-weight-medium">Running</div><div class="text-caption axo-muted">{{ runningText }}</div></div>
            </v-expansion-panel-title>
            <v-expansion-panel-text>
              <v-radio-group v-if="senders.length > 1" v-model="d.join" inline hide-details density="compact" :disabled="readonly" class="mb-2">
                <template #label><span class="text-body-2">Starts</span></template>
                <v-radio value="all" label="when every step before it finished well" />
                <v-radio value="arrived" label="with whatever arrived" />
              </v-radio-group>
              <div class="axo-step-dlg__grid">
                <template v-if="d.kind !== 'choreography'">
                <v-text-field :model-value="d.max_attempts ?? ''" type="number" min="1" max="10" density="compact" variant="outlined"
                              label="Tries in all" placeholder="the function's" persistent-placeholder hide-details="auto" :disabled="readonly"
                              :hint="hint('max_attempts')" @update:model-value="num('max_attempts', $event)" />
                <v-select v-model="d.retry_backoff" :items="[{ value: null, title: 'the function\'s' }, { value: 'fixed', title: 'fixed' }, { value: 'exponential', title: 'doubling' }]"
                          density="compact" variant="outlined" label="Wait between tries" hide-details :disabled="readonly" />
                <v-text-field :model-value="d.retry_delay_seconds ?? ''" type="number" min="0" density="compact" variant="outlined" label="First wait (s)"
                              placeholder="the function's" persistent-placeholder hide-details :disabled="readonly" @update:model-value="num('retry_delay_seconds', $event)" />
                </template>
                <v-text-field :model-value="d.timeout_seconds ?? ''" type="number" min="1" density="compact" variant="outlined"
                              :label="d.kind === 'choreography' ? 'Longest run (s)' : 'Longest call (s)'"
                              placeholder="the VE's limit" persistent-placeholder hide-details :disabled="readonly" @update:model-value="num('timeout_seconds', $event)" />
                <v-text-field :model-value="d.max_wait ?? ''" type="number" density="compact" variant="outlined"
                              :label="d.kind === 'choreography' ? 'May wait in its line (s)' : 'May wait for a runner (s)'"
                              placeholder="the VE's default" persistent-placeholder hide-details="auto" :disabled="readonly"
                              hint="-1 forever, 0 not at all" persistent-hint @update:model-value="num('max_wait', $event)" />
              </div>
              <div v-if="d.kind === 'choreography'" class="text-caption axo-muted mt-3">
                Its own steps retry as they are set. A retry of this step, or a resume of this run, resumes the same run of
                {{ d.choreography_name || 'it' }} (only what failed runs again). The longest run covers the whole of it.
              </div>
            </v-expansion-panel-text>
          </v-expansion-panel>

          <!-- files -->
          <v-expansion-panel v-if="d.kind === 'bucket' || filesFrom" value="files">
            <v-expansion-panel-title>
              <div><div class="font-weight-medium">Files</div><div class="text-caption axo-muted">{{ filesText }}</div></div>
            </v-expansion-panel-title>
            <v-expansion-panel-text>
              <template v-if="d.kind === 'bucket' && role === 'source'">
                <div class="text-body-2 mb-2">A run started by hand reads:</div>
                <ChoreographyFilesPicker v-if="d.bucket_name" :bucket="d.bucket_name" :model-value="d.selected_items ?? null" :readonly="readonly"
                                         @update:model-value="d.selected_items = $event" />
                <div class="text-caption axo-muted mt-2">A run started by a new file reads only that file.</div>
              </template>
              <template v-else-if="d.kind === 'bucket'">
                <v-switch v-model="d.save_result" color="primary" inset density="compact" hide-details :disabled="readonly"
                          label="Also save the step's JSON result as a file" />
                <v-text-field v-if="d.save_result !== false" v-model="d.key_pattern" density="compact" variant="outlined" label="File name"
                              :placeholder="keyPlaceholder" persistent-placeholder :disabled="readonly" class="mt-2" hide-details="auto"
                              hint="{choreography} {run_id} {step} {source} {item} {date} are filled in" persistent-hint />
                <div class="text-caption axo-muted mt-3">Files the earlier steps' jobs write into this bucket during the run are {{ role === 'store_pass' ? 'passed on' : 'kept' }} either way.</div>
              </template>
              <template v-else>
                <v-radio-group v-model="d.files_mode" hide-details density="compact" :disabled="readonly">
                  <v-radio value="per_file" :label="d.kind === 'choreography' ? 'One run per file' : 'Once per file'" />
                  <v-radio value="all_at_once" :label="d.kind === 'choreography' ? 'One run with every file' : 'Once, with the list of every file'" />
                </v-radio-group>
                <v-slider v-if="(d.files_mode ?? 'per_file') === 'per_file'" v-model="d.parallelism" :min="1" :max="32" :step="1" thumb-label
                          color="primary" hide-details class="mt-3" :disabled="readonly">
                  <template #prepend><span class="text-body-2 text-no-wrap">At the same time</span></template>
                  <template #append><span class="text-body-2">{{ d.parallelism ?? 4 }}</span></template>
                </v-slider>
              </template>
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>

        <v-alert v-for="p in problems" :key="p" :type="'warning'" variant="tonal" density="compact" class="mt-3">{{ p }}</v-alert>
      </v-card-text>
      <v-card-actions>
        <v-btn v-if="!readonly" color="error" variant="text" prepend-icon="mdi-delete-outline" @click="emit('remove', node.node_id)">Delete step</v-btn>
        <v-spacer />
        <v-btn variant="text" @click="emit('close')">{{ readonly ? 'Close' : 'Cancel' }}</v-btn>
        <v-btn v-if="!readonly" color="primary" variant="flat" @click="done">Done</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// A step's dialog: what it runs, its parameters (where each comes from, in
// plain words, and how one that several earlier steps send is picked), how
// it runs (tries, waits, limits) and its files (a bucket's role, or once per
// file after a bucket). Parameters open first. Done applies to the draft.
import type { ChGraph, ChNode, Choreography, DesignerInfo, ParamMerge, StepInfo, StepParamInfo, StepState } from '~/utils/choreographies'
import type { FunctionRow, ParamSpec } from '~/utils/functions'
import type { ActiveObject } from '~/utils/objects'

const props = defineProps<{
  node: ChNode | null; graph: ChGraph; info: DesignerInfo | null; step?: StepInfo | null; readonly?: boolean
  functions: FunctionRow[]; objects: ActiveObject[]; methodsOf: (o: ActiveObject) => string[]; buckets: string[]
  paramsOf: (o: ActiveObject, method: string) => ParamSpec[] | null
  lastRun?: StepState | null; problems?: string[]; fname: (id: string) => string; title: (id: string) => string
  /** The choreography being edited: a choreography step can't call it (nor one that calls it back). */
  selfId?: string | null
}>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'save', v: ChNode): void; (e: 'remove', id: string): void }>()

const d = ref<ChNode | null>(null)
const open = ref<string[]>(['params'])
watch(() => props.node, (n) => {
  d.value = n ? JSON.parse(JSON.stringify(n)) : null
  if (n) open.value = n.kind === 'bucket' ? ['what', 'files'] : n.kind === 'choreography' ? ['what', 'params'] : ['params']
}, { immediate: true })

const role = computed(() => bucketRole(props.graph, props.node?.node_id ?? ''))
const ROLE_HELP: Record<string, string> = {
  source: 'Nothing comes before it, so it is where the run starts: the steps after it get its files.',
  store: 'Nothing comes after it: it saves what the steps before it give.',
  store_pass: 'It saves what the steps before it give, then passes on only the files of this run.',
}
const senders = computed(() => props.step?.senders ?? props.graph.edges.filter(e => e.target_node_id === props.node?.node_id && !e.on_failure)
  .map(e => e.source_node_id).filter(id => props.graph.nodes.find(n => n.node_id === id)?.kind !== 'bucket'))
const filesFrom = computed(() => props.step?.files_from ?? props.graph.edges.find(e => e.target_node_id === props.node?.node_id
  && props.graph.nodes.find(n => n.node_id === e.source_node_id)?.kind === 'bucket')?.source_node_id ?? null)
const isFirst = computed(() => !props.graph.edges.some(e => e.target_node_id === props.node?.node_id))

// ── what it runs ────────────────────────────────────────────────────────────
const functionItems = computed(() => props.functions.filter(f => !f.deleted).map(f => ({ value: f.function_id, title: f.name })))
const fnRow = computed(() => props.functions.find(f => f.function_id === d.value?.function_id))
const versionItems = computed(() => [{ value: null, title: 'The newest, when it runs' },
  ...(fnRow.value?.versions ?? []).filter(v => !v.deleted_at).map(v => ({ value: v.version, title: `v${v.version}` }))])
function pickFunction(id: string) {
  if (!d.value) return
  d.value.function_id = id
  d.value.function_name = props.functions.find(f => f.function_id === id)?.name ?? null
  d.value.function_version = null
}
const objRow = computed(() => props.objects.find(o => o.name === d.value?.object_name))
const methodItems = computed(() => (objRow.value ? props.methodsOf(objRow.value) : ['run']))
function pickObject(name: string) { if (d.value) { d.value.object_name = name; d.value.method = null } }

// a choreography step: which one, its version, a preview of its steps
const { id: veId, api } = useCurrentVe()
const { rows: chRows, byId: chById } = useChoreographies()
const chItems = computed(() => {
  const blocked = loopMakers(chRows.value, props.selfId ?? null)
  return chRows.value.filter(c => !blocked.has(c.choreography_id) || c.choreography_id === d.value?.choreography_id)
    .map(c => ({ value: c.choreography_id, title: c.name }))
})
const chVersions = ref<number[]>([])
const inner = ref<Choreography | null>(null)
const innerGraph = computed(() => (inner.value ? (needsLayout(inner.value.graph) ? arrange(inner.value.graph) : inner.value.graph) : null))
const loadingInner = ref(false)
const chVersionItems = computed(() => {
  const newest = chById(d.value?.choreography_id ?? '')?.version
  return [{ value: null, title: newest ? `The newest (now v${newest})` : 'The newest' },
    ...[...chVersions.value].sort((a, b) => b - a).map(v => ({ value: v, title: `v${v}` }))]
})
let innerSeq = 0
watch(() => [d.value?.kind, d.value?.choreography_id, d.value?.choreography_version], async () => {
  const n = d.value
  inner.value = null
  if (n?.kind !== 'choreography' || !n.choreography_id) { chVersions.value = []; return }
  const seq = ++innerSeq
  loadingInner.value = true
  const ref_ = encodeURIComponent(n.choreography_id)
  try {
    const [doc, versions] = await Promise.all([
      api<Choreography>(`/choreographies/${ref_}`, { query: n.choreography_version ? { version: n.choreography_version } : {} }),
      api<Array<{ version: number }>>(`/choreographies/${ref_}/versions`).catch(() => []),
    ])
    if (seq !== innerSeq) return
    inner.value = doc
    chVersions.value = versions.map(v => v.version)
  } catch { /* gone, or not visible: the check says so */ } finally { if (seq === innerSeq) loadingInner.value = false }
}, { immediate: true })
function pickChoreography(id: string) {
  if (!d.value) return
  d.value.choreography_id = id
  d.value.choreography_name = chById(id)?.name ?? null
  d.value.choreography_version = null
}

// ── parameters ──────────────────────────────────────────────────────────────
const schema = computed<ParamSpec[] | null>(() => {
  if (d.value?.kind === 'function') {
    const v = fnRow.value?.versions.find(x => x.version === d.value?.function_version) ?? fnRow.value?.latest
    return v?.params_schema ?? null
  }
  if (d.value?.kind === 'object' && objRow.value) {
    return props.paramsOf(objRow.value, d.value.method || 'run')
  }
  return null
})
const known = computed(() => !!props.step?.params || !!schema.value)
const params = computed<StepParamInfo[]>(() => {
  const fromCheck = props.step?.params ?? []
  const fromSchema = (schema.value ?? []).map(s => ({ name: s.name, required: s.required, default: s.default }))
  const names = new Map<string, StepParamInfo>()
  for (const p of [...fromSchema, ...fromCheck]) names.set(p.name, { ...names.get(p.name), ...p })
  for (const k of Object.keys(d.value?.params ?? {})) if (!names.has(k)) names.set(k, { name: k, required: false })
  return [...names.values()].filter(p => !['ctx', 'self'].includes(p.name))
})
const isSet = (name: string) => !!d.value?.params && name in d.value.params
function originOf(p: StepParamInfo): { text: string; color: string } {
  if (isSet(p.name)) return { text: 'Set here', color: 'primary' }
  if (p.name === 'data' && filesFrom.value) return { text: `The file from ${props.title(filesFrom.value)}`, color: 'brand-3' }
  if (senders.value.length) return { text: `From ${senders.value.map(props.title).join(' / ')} (if sent)`, color: 'info' }
  if (isFirst.value) return p.required ? { text: "From the run's input", color: 'warning' } : { text: "Run input, else the code's default", color: 'muted' }
  if (p.default !== undefined) return { text: `Code default: ${short(p.default)}`, color: 'muted' }
  return p.required ? { text: 'Missing', color: 'error' } : { text: "The code's default", color: 'muted' }
}
const missing = computed(() => params.value.filter(p => p.required && originOf(p).color === 'error').map(p => p.name))
function specOf(p: StepParamInfo): ParamSpec {
  return schema.value?.find(s => s.name === p.name) ?? { name: p.name, type: 'json', required: p.required, default: p.default }
}
function setHere(p: StepParamInfo) { if (d.value) d.value.params = { ...(d.value.params ?? {}), [p.name]: p.default ?? null } }
function unset(name: string) { if (d.value?.params) { const { [name]: _x, ...rest } = d.value.params; d.value.params = rest } }
function setParam(name: string, v: unknown) {
  if (!d.value) return
  if (v === undefined) unset(name)
  else d.value.params = { ...(d.value.params ?? {}), [name]: v }
}
function setMerge(key: string, rule: ParamMerge | null) {
  if (!d.value) return
  const m = { ...(d.value.merge ?? {}) }
  if (rule && !(key === '*' && rule.strategy === 'priority' && !rule.order?.length)) m[key] = rule
  else delete m[key]
  d.value.merge = m
}
const short = (v: unknown) => { const t = typeof v === 'string' ? v : JSON.stringify(v); return t && t.length > 60 ? `${t.slice(0, 57)}…` : t }

// ── running / files ─────────────────────────────────────────────────────────
const hint = (name: string) => props.info?.step_settings.find(s => s.name === name)?.description
function num(key: 'max_attempts' | 'retry_delay_seconds' | 'timeout_seconds' | 'max_wait', v: string | number) {
  if (!d.value) return
  const n = v === '' || v === null ? null : Number(v)
  d.value[key] = n === null || !Number.isFinite(n) ? null : n
}
const runningText = computed(() => {
  const n = d.value
  if (!n) return ''
  const parts = n.kind === 'choreography' ? ['its own run'] : [n.max_attempts ? `${n.max_attempts} tries` : 'tries as the function says']
  if (n.timeout_seconds) parts.push(`at most ${n.timeout_seconds} s`)
  if (senders.value.length > 1) parts.push(n.join === 'arrived' ? 'starts with what arrived' : 'waits for all')
  return parts.join(' · ')
})
const filesText = computed(() => {
  const n = d.value
  if (!n) return ''
  if (n.kind === 'bucket' && role.value === 'source') return n.selected_items?.length ? `${n.selected_items.length} picked files` : 'Every file'
  if (n.kind === 'bucket') return n.save_result === false ? 'Keeps only the files jobs write' : 'Saves the results as files'
  if (n.kind === 'choreography') return (n.files_mode ?? 'per_file') === 'per_file' ? `One run per file, ${n.parallelism ?? 4} at a time` : 'One run with every file'
  return (n.files_mode ?? 'per_file') === 'per_file' ? `Once per file, ${n.parallelism ?? 4} at a time` : 'Once, with every file'
})
const keyPlaceholder = computed(() => props.info?.step_settings.find(s => s.name === 'key_pattern')?.placeholder ?? '{choreography}/{run_id}/{source}.json')

function done() {
  if (!d.value) return
  const n = { ...d.value }
  if (!n.label) delete n.label
  emit('save', n)
}
</script>


<style scoped>
.axo-step-dlg__param { padding: 10px 0; border-bottom: 1px solid rgba(var(--v-border-color), 0.08); }
.axo-step-dlg__param:last-child { border-bottom: none; }
.axo-step-dlg__all { padding: 10px 12px; border-radius: 8px; background: rgba(var(--v-theme-info), 0.05); }
.axo-step-dlg__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
</style>
