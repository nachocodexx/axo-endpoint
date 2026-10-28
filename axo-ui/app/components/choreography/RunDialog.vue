<template>
  <v-dialog :model-value="modelValue" max-width="720" scrollable @update:model-value="emit('update:modelValue', $event)">
    <v-card v-if="doc">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-play-circle-outline" color="primary" /> Run {{ doc.name }}
        <v-spacer />
        <AxoHelp title="Running a choreography" text="The input fills the first steps' parameters by name (and is the run's event: ctx.event / self.event). Steps that read a bucket read the files picked on them, unless you change them for this run." />
      </v-card-title>
      <v-card-text>
        <v-select v-model="version" :items="versionItems" density="compact" variant="outlined" label="Version" hide-details class="mb-4" style="max-width: 280px" />
        <AxoSkeleton v-if="loadingVersion" :lines="3" />
        <template v-else-if="shown">
          <!-- input -->
          <div class="d-flex align-center mb-2">
            <span class="text-subtitle-2">Input</span>
            <v-spacer />
            <v-btn-toggle v-model="mode" density="compact" variant="outlined" divided mandatory>
              <v-btn value="fields" size="small">Fields</v-btn>
              <v-btn value="json" size="small">Edit as JSON</v-btn>
            </v-btn-toggle>
          </div>
          <template v-if="mode === 'fields'">
            <div v-if="!inputs.length" class="text-body-2 axo-muted mb-2">The first steps need nothing more: every parameter is set on them.</div>
            <div v-for="p in inputs" :key="p.name" class="mb-3">
              <ParamField :spec="p.spec" :model-value="values[p.name]" @update:model-value="setValue(p.name, $event)"
                          @invalid="(m: string | null) => m ? (invalid[p.name] = m) : delete invalid[p.name]" />
              <div class="text-caption axo-muted mt-1">For {{ p.steps.map(title).join(', ') }}{{ p.required ? ' · needed' : '' }}</div>
            </div>
          </template>
          <v-textarea v-else v-model="jsonText" variant="outlined" rows="6" auto-grow class="axo-mono" :error-messages="jsonProblem ?? undefined" hide-details="auto" />

          <!-- starting buckets -->
          <div v-for="b in startBuckets" :key="b.node_id" class="axo-run-dlg__bucket mt-4">
            <div class="d-flex align-center flex-wrap ga-2">
              <v-icon icon="mdi-bucket-outline" color="brand-3" />
              <span class="font-weight-medium">{{ title(b.node_id) }}</span>
              <span class="axo-muted text-body-2">{{ b.bucket_name }}</span>
              <v-spacer />
              <v-btn v-if="!(b.node_id in files)" size="small" variant="text" prepend-icon="mdi-pencil" @click="files[b.node_id] = b.selected_items ?? null">Change for this run</v-btn>
              <v-btn v-else size="small" variant="text" @click="delete files[b.node_id]">Use the step's files</v-btn>
            </div>
            <div v-if="!(b.node_id in files)" class="text-body-2 mt-1">
              Reads {{ b.selected_items?.length ? b.selected_items.map(f => f.name).join(', ') : 'every file' }}
            </div>
            <ChoreographyFilesPicker v-else :bucket="b.bucket_name!" :model-value="files[b.node_id] ?? null" all-label="Every file"
                                     class="mt-2" @update:model-value="files[b.node_id] = $event" />
            <div v-if="readCount(b) === null" class="text-caption text-warning mt-1">It will read every file in {{ b.bucket_name }} — one call per file for the step after it.</div>
            <div v-else-if="(readCount(b) ?? 0) > 10" class="text-caption text-warning mt-1">It will read {{ readCount(b) }} files.</div>
          </div>

          <!-- more -->
          <v-expansion-panels class="mt-4" variant="accordion">
            <v-expansion-panel title="More">
              <v-expansion-panel-text>
                <v-text-field v-model="runId" density="compact" variant="outlined" label="Run id" placeholder="made for you" persistent-placeholder
                              hint="The same id twice runs once (safe to retry)." persistent-hint />
                <div class="text-body-2 mt-3">Step answers are kept for {{ keepTitle(doc.settings.keep_results).toLowerCase() }} (Settings).</div>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>

          <v-alert v-for="w in startWarnings" :key="w" type="warning" variant="tonal" density="compact" class="mt-3">{{ w }}</v-alert>
          <v-alert v-if="queueText" type="info" variant="tonal" density="compact" class="mt-3">{{ queueText }}</v-alert>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">Cancel</v-btn>
        <AxoBtn variant="flat" :loading="starting" :disabled="!canRun" prepend-icon="mdi-play" @click="run">Run</AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// Starts a run: a version (the newest by default), the input (a field per
// parameter the first steps don't set, or JSON), the files the starting
// bucket steps read (changed for this run only), an own run id. Opens the
// live run page.
import type { ChNode, Choreography, StepParamInfo } from '~/utils/choreographies'
import type { VersionRow } from '~/composables/useCurrentChoreography'
import type { ParamSpec } from '~/utils/functions'
import type { PickedFiles } from './FilesPicker.vue'

const props = defineProps<{ modelValue: boolean; doc: Choreography | null; versions: VersionRow[]; initialVersion?: number | null; initialInput?: unknown }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()
const { id: veId, api } = useCurrentVe()
const notifier = useNotifier()
const { nameOf } = useFunctions()

const version = ref<number>(0)
const shown = ref<Choreography | null>(null)
const loadingVersion = ref(false)
const versionItems = computed(() => [...props.versions].sort((a, b) => b.version - a.version)
  .map(v => ({ value: v.version, title: `v${v.version}${v.version === props.doc?.latest_version ? ' (newest)' : ''}` })))
watch(() => props.modelValue, (open) => {
  if (!open || !props.doc) return
  version.value = props.initialVersion ?? props.doc.latest_version ?? props.doc.version
  runId.value = ''
  for (const k of Object.keys(files)) delete files[k]
  for (const k of Object.keys(values)) delete values[k]
  if (props.initialInput && typeof props.initialInput === 'object') Object.assign(values, props.initialInput)
  jsonText.value = JSON.stringify(props.initialInput ?? {}, null, 2)
  mode.value = props.initialInput && typeof props.initialInput !== 'object' ? 'json' : 'fields'
}, { immediate: true })
watch(version, async (v) => {
  if (!props.doc || !v) return
  if (v === props.doc.version) { shown.value = props.doc; return }
  loadingVersion.value = true
  try { shown.value = await api<Choreography>(`/choreographies/${encodeURIComponent(props.doc.name)}`, { query: { version: v } }) }
  catch (err) { notifier.error(err, "That version couldn't load") } finally { loadingVersion.value = false }
}, { immediate: true })

const title = (id: string) => { const n = shown.value?.graph.nodes.find(x => x.node_id === id); return n ? nodeTitle(n, nameOf) : id }
const firsts = computed(() => (shown.value ? firstSteps(shown.value.graph) : []))
const startBuckets = computed(() => firsts.value.filter(n => n.kind === 'bucket'))

// a field per parameter the first process steps don't set (and no bucket fills)
const typeOf = (v: unknown) => (typeof v === 'number' ? 'number' : typeof v === 'boolean' ? 'boolean' : typeof v === 'string' ? 'string' : 'json')
const inputs = computed(() => {
  const out = new Map<string, { name: string; required: boolean; steps: string[]; spec: ParamSpec }>()
  for (const n of firsts.value) {
    if (n.kind === 'bucket') continue
    for (const p of (shown.value?.steps[n.node_id]?.params ?? []) as StepParamInfo[]) {
      if (p.value !== undefined || p.name === 'data') continue
      const cur = out.get(p.name)
      if (cur) { cur.steps.push(n.node_id); cur.required ||= p.required; continue }
      out.set(p.name, { name: p.name, required: p.required, steps: [n.node_id],
        spec: { name: p.name, type: p.default === undefined ? 'json' : typeOf(p.default), required: p.required, default: p.default } })
    }
  }
  return [...out.values()]
})

const mode = ref<'fields' | 'json'>('fields')
const values = reactive<Record<string, unknown>>({})
const invalid = reactive<Record<string, string>>({})
const jsonText = ref('{}')
const jsonProblem = ref<string | null>(null)
function setValue(name: string, v: unknown) { if (v === undefined) delete values[name]; else values[name] = v }
watch(mode, (m) => {
  if (m === 'json') jsonText.value = JSON.stringify(current(), null, 2)
  else { const parsed = parsed_(); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) { for (const k of Object.keys(values)) delete values[k]; Object.assign(values, parsed) } }
})
function parsed_(): unknown {
  try { jsonProblem.value = null; return jsonText.value.trim() ? JSON.parse(jsonText.value) : {} } catch (e) { jsonProblem.value = `Not valid JSON: ${(e as Error).message}`; return undefined }
}
watch(jsonText, () => { if (mode.value === 'json') parsed_() })
const current = () => Object.fromEntries(Object.entries(values).filter(([, v]) => v !== undefined))
const input = computed(() => (mode.value === 'json' ? parsed_() : current()))

const files = reactive<Record<string, PickedFiles>>({})
const readCount = (b: ChNode) => {
  const picked = b.node_id in files ? files[b.node_id] : b.selected_items ?? null
  return picked === null || picked === undefined ? null : picked.length
}
const missingInputs = computed(() => (mode.value === 'fields' ? inputs.value.filter(p => p.required && values[p.name] === undefined).map(p => p.name) : []))
const startWarnings = computed(() => {
  const out: string[] = []
  if (missingInputs.value.length) out.push(`Fill in ${missingInputs.value.join(', ')}: the first steps need ${missingInputs.value.length === 1 ? 'it' : 'them'}.`)
  for (const [node, f] of Object.entries(files)) if (Array.isArray(f) && !f.length) out.push(`Pick at least one file for ${title(node)}, or every file.`)
  return out
})
const queueText = computed(() => {
  const d = props.doc
  if (!d?.active_runs) return ''
  const limit = d.settings.max_parallel_runs
  return d.active_runs >= limit ? `${d.active_runs} runs are going and at most ${limit} may go at once: this one waits in line.` : `${d.active_runs} run${d.active_runs === 1 ? ' is' : 's are'} going now.`
})
const canRun = computed(() => !!shown.value && !starting.value && !startWarnings.value.length && !Object.keys(invalid).length
  && !(mode.value === 'json' && jsonProblem.value))

const runId = ref('')
const starting = ref(false)
async function run() {
  if (!props.doc || !canRun.value) return
  starting.value = true
  try {
    const body: Record<string, unknown> = { version: version.value, input: input.value }
    if (runId.value.trim()) body.run_id = runId.value.trim()
    const f = Object.fromEntries(Object.entries(files).map(([k, v]) => [k, v === null ? 'all' : v]))
    if (Object.keys(f).length) body.files = f
    const started = await api<{ run_id: string }>(`/choreographies/${encodeURIComponent(props.doc.name)}/runs`, { method: 'POST', body })
    emit('update:modelValue', false)
    await navigateTo(`/ves/${veId.value}/choreographies/${encodeURIComponent(props.doc.name)}/runs/${started.run_id}`)
  } catch (err) {
    notifier.error(err, "The run couldn't start")
  } finally { starting.value = false }
}
</script>

<style scoped>
.axo-run-dlg__bucket { padding: 10px 12px; border-radius: 10px; border: 1px solid rgba(var(--v-border-color), 0.14); }
</style>
