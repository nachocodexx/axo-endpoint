<template>
  <v-navigation-drawer :model-value="!!open" location="right" temporary :width="smAndDown ? 340 : 440" @update:model-value="!$event && emit('close')">
    <!-- an arrow -->
    <div v-if="open?.kind === 'edge' && edge" class="pa-4">
      <div class="d-flex align-center mb-3">
        <span class="text-subtitle-1 font-weight-medium text-truncate">{{ title(edge.source_node_id) }} → {{ title(edge.target_node_id) }}</span>
        <v-spacer /><v-btn icon="mdi-close" size="small" variant="text" aria-label="Close" @click="emit('close')" />
      </div>
      <v-chip :color="edgeState === 'taken' ? 'primary' : 'muted'" variant="tonal" size="small"
              :prepend-icon="edgeState === 'taken' ? 'mdi-check' : edgeState === 'not_taken' ? 'mdi-close' : 'mdi-timer-sand'">
        {{ edgeState === 'taken' ? 'Followed' : edgeState === 'not_taken' ? 'Not followed' : 'Not decided yet' }}
      </v-chip>
      <p class="text-body-2 mt-3">{{ edgeWhy }}</p>
    </div>

    <!-- a step -->
    <div v-else-if="open?.kind === 'node' && node" class="pa-4">
      <div class="d-flex align-center mb-1">
        <v-icon :icon="KIND_INFO[node.kind].icon" :color="KIND_INFO[node.kind].color" class="mr-2" />
        <span class="text-subtitle-1 font-weight-medium text-truncate">{{ title(node.node_id) }}</span>
        <v-spacer /><v-btn icon="mdi-close" size="small" variant="text" aria-label="Close" @click="emit('close')" />
      </div>
      <div class="text-caption axo-muted mb-3">{{ nodeSubtitle(node, fname, graph, chById) }}</div>
      <div class="d-flex flex-wrap ga-2 mb-3">
        <AxoStatusChip kind="run" :value="state?.status ?? 'pending'" />
        <v-chip v-if="state && state.attempt > 1" size="small" variant="tonal" color="warning">try {{ state.attempt }}{{ node.max_attempts ? ` of ${node.max_attempts}` : '' }}</v-chip>
      </div>
      <AxoKeyValue :items="facts" :columns="2" class="mb-3" />

      <!-- a choreography step: its own run -->
      <div v-if="node.kind === 'choreography' && child" class="axo-panel__child mb-3">
        <div class="d-flex align-center ga-2 mb-2">
          <v-icon icon="mdi-layers-triple-outline" color="success" size="18" />
          <span class="text-body-2 font-weight-medium text-truncate">The run of {{ child.choreography_name }} · v{{ child.version }}</span>
          <v-spacer />
          <AxoStatusChip kind="run" :value="child.status" />
        </div>
        <div class="text-caption axo-muted mb-2">{{ childText(child) }}</div>
        <AxoSkeleton v-if="!childRun && loadingChild" :lines="2" />
        <v-list v-else-if="childSteps.length" density="compact" class="pa-0 bg-transparent">
          <v-list-item v-for="st in childSteps" :key="st.node_id" :title="st.node_id" min-height="30"
                       :subtitle="[st.duration_ms != null ? formatMs(st.duration_ms) : '', st.error_name ?? ''].filter(Boolean).join(' · ')">
            <template #prepend>
              <AxoRunningRing v-if="stepLook(st.status).ring" :size="14" class="mr-2" />
              <v-icon v-else :icon="stepLook(st.status).icon" :color="stepLook(st.status).color" size="16" class="mr-2" />
            </template>
          </v-list-item>
        </v-list>
        <v-btn size="small" variant="tonal" color="success" prepend-icon="mdi-open-in-app" class="mt-2" :to="childLink(child)">
          Open the run of {{ child.choreography_name }}
        </v-btn>
      </div>

      <div v-if="answerJob" class="d-flex flex-wrap ga-2 mb-3">
        <v-btn v-if="state?.job_id" size="small" variant="tonal" prepend-icon="mdi-briefcase-outline" :to="`/ves/${veId}/jobs/${state.job_id}`">Open the job</v-btn>
        <v-btn v-if="state?.status === 'completed'" size="small" variant="text" prepend-icon="mdi-download" :loading="downloading" @click="download">Answer (JSON)</v-btn>
      </div>

      <v-alert v-if="state?.error" type="error" variant="tonal" density="compact" class="mb-3">
        <AxoErrorName v-if="state.error_name" :name="state.error_name" class="mb-1" />
        <div class="text-body-2">{{ state.error }}</div>
      </v-alert>
      <v-alert v-for="w in state?.warnings ?? []" :key="w" type="warning" variant="tonal" density="compact" class="mb-2">{{ w }}</v-alert>

      <template v-if="state && Object.keys(state.params ?? {}).length">
        <div class="text-subtitle-2 mt-2 mb-1">Parameters</div>
        <div v-for="(v, k) in state.params" :key="k" class="axo-panel__param">
          <div class="d-flex align-center ga-2"><span class="axo-mono">{{ k }}</span>
            <v-chip size="x-small" variant="tonal">{{ originText(state.param_origins?.[k], title) }}</v-chip></div>
          <div class="axo-mono text-caption text-truncate" :title="full(v)">{{ full(v) }}</div>
        </div>
      </template>

      <template v-if="state?.status === 'completed'">
        <div class="text-subtitle-2 mt-4 mb-1">Answer <span class="axo-muted text-caption">{{ formatBytes(state.output_bytes) }}</span></div>
        <AxoSkeleton v-if="loadingAnswer" :lines="2" />
        <StateValue v-else-if="answer !== undefined" :value="answer" />
        <div v-else class="text-body-2 axo-muted">{{ answerProblem || 'Not available' }}</div>
      </template>

      <template v-if="state?.files?.length">
        <div class="text-subtitle-2 mt-4 mb-1">Files</div>
        <v-list density="compact" class="pa-0">
          <v-list-item v-for="f in state.files" :key="`${f.bucket}/${f.name}@${f.version}`" prepend-icon="mdi-file-outline"
                       :title="f.name" :subtitle="`${f.bucket}${f.version ? ` · v${f.version}` : ''}${f.size != null ? ` · ${formatBytes(f.size)}` : ''}`"
                       :to="`/buckets/${f.bucket}?path=${encodeURIComponent(f.name.includes('/') ? f.name.slice(0, f.name.lastIndexOf('/') + 1) : '')}`" />
        </v-list>
      </template>

      <template v-if="state?.items?.length">
        <div class="d-flex align-center mt-4 mb-1">
          <span class="text-subtitle-2">Per file</span><v-spacer />
          <span class="text-caption axo-muted">{{ itemsDone }} / {{ state.items.length }} done</span>
        </div>
        <v-list density="compact" class="pa-0">
          <v-list-item v-for="it in state.items" :key="it.index" :title="it.file?.name"
                       :to="it.child ? childLink(it.child) : it.job_id ? `/ves/${veId}/jobs/${it.job_id}` : undefined"
                       :subtitle="[it.child ? childText(it.child) : '', it.attempt > 1 ? `try ${it.attempt}` : '', it.duration_ms != null ? formatMs(it.duration_ms) : '', it.error_name ?? ''].filter(Boolean).join(' · ')">
            <template #prepend>
              <AxoRunningRing v-if="stepLook(it.status).ring" :size="16" class="mr-2" />
              <v-icon v-else :icon="stepLook(it.status).icon" :color="stepLook(it.status).color" size="18" class="mr-2" />
            </template>
          </v-list-item>
        </v-list>
      </template>
    </div>
  </v-navigation-drawer>
</template>

<script setup lang="ts">
// The run page's side panel: a clicked step (times, tries, its job, the
// parameters it got and where each came from, its answer, the files it
// wrote, each file's call, its error) or a clicked arrow (followed or not,
// and why). A choreography step: its own run's state, a short list of that
// run's steps and a link to it (one per file when it runs once per file).
import { useDisplay } from 'vuetify'
import type { CanvasPick } from './Canvas.vue'
import type { ChGraph, ChildRun, Run } from '~/utils/choreographies'

const props = defineProps<{ open: CanvasPick; run: Run; graph: ChGraph; fname: (id: string) => string; title: (id: string) => string }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const { id: veId, api } = useCurrentVe()
const notifier = useNotifier()
const { smAndDown } = useDisplay()
const { byId: chById } = useChoreographies()

const node = computed(() => (props.open?.kind === 'node' ? props.graph.nodes.find(n => n.node_id === props.open!.id) ?? null : null))
const state = computed(() => (node.value ? props.run.steps?.[node.value.node_id] ?? null : null))
const edge = computed(() => (props.open?.kind === 'edge' ? props.graph.edges.find(e => e.edge_id === props.open!.id) ?? null : null))
const edgeState = computed(() => (edge.value ? props.run.edge_states?.[edge.value.edge_id] : undefined))
const edgeWhy = computed(() => {
  const e = edge.value
  if (!e) return ''
  const src = props.run.steps?.[e.source_node_id]
  const st = edgeState.value
  if (e.on_failure) return st === 'taken' ? `${props.title(e.source_node_id)} failed, so the error went on to ${props.title(e.target_node_id)}.` : `An “on failure” arrow: followed only when ${props.title(e.source_node_id)} fails.`
  if (!st) return src && ['pending', 'running'].includes(src.status) ? `${props.title(e.source_node_id)} hasn't finished yet.` : `${props.title(e.source_node_id)} didn't finish well, so its arrows weren't followed.`
  if (e.otherwise) return st === 'taken' ? 'None of the step’s rules matched, so “otherwise” was followed.' : 'One of the step’s rules matched, so “otherwise” wasn’t followed.'
  if (e.condition?.rules?.length) return `${st === 'taken' ? 'The answer matched' : "The answer didn't match"}: ${conditionText(e.condition)}.`
  return 'A plain arrow: followed whenever the step finishes well.'
})

const facts = computed(() => {
  const s = state.value
  if (!s) return []
  return [
    { label: 'Started', value: s.started_at ? formatExactTime(ts(s.started_at)) : '—' },
    { label: 'Took', value: s.duration_ms != null ? formatMs(s.duration_ms) : s.started_at && s.status === 'running' ? 'still running' : '—' },
    ...(s.target_version ? [{ label: 'Version', value: `v${s.target_version}` }] : []),
    ...(s.endpoint_id ? [{ label: 'Endpoint', value: s.endpoint_id, mono: true }] : []),
  ]
})
const child = computed(() => state.value?.child ?? null)
const childLink = (c: ChildRun) => `/ves/${veId.value}/choreographies/${encodeURIComponent(chById(c.choreography_id)?.name ?? c.choreography_name)}/runs/${c.run_id}`
const childRun = ref<Run | null>(null)
const loadingChild = ref(false)
watch(() => [child.value?.run_id, child.value?.status, child.value?.done], async () => {
  const c = child.value
  if (!c) { childRun.value = null; return }
  loadingChild.value = true
  try { childRun.value = await api<Run>(`/choreographies/${encodeURIComponent(c.choreography_id)}/runs/${c.run_id}`) }
  catch { childRun.value = null } finally { loadingChild.value = false }
}, { immediate: true })
const childSteps = computed(() => Object.values(childRun.value?.steps ?? {}).filter(s => s.kind !== 'bucket'))
// the job whose result is the step's answer (a choreography step: its run's answer step's)
const answerJob = computed(() => state.value?.job_id ?? state.value?.child?.answer_job_id ?? null)
const itemsDone = computed(() => (state.value?.items ?? []).filter(i => !['pending', 'running'].includes(i.status)).length)
const full = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v))

const answer = ref<unknown>(undefined)
const answerProblem = ref('')
const loadingAnswer = ref(false)
watch(() => [answerJob.value, state.value?.status], async () => {
  answer.value = undefined
  answerProblem.value = ''
  const s = state.value
  const job = answerJob.value
  if (!job || s?.status !== 'completed') return
  loadingAnswer.value = true
  try { answer.value = await api(`/jobs/${job}/result`) } catch (err) { answerProblem.value = AxoApiError.from(err).message } finally { loadingAnswer.value = false }
}, { immediate: true })
const downloading = ref(false)
async function download() {
  const s = state.value
  if (!s || !answerJob.value) return
  downloading.value = true
  try {
    const value = answer.value !== undefined ? answer.value : await api(`/jobs/${answerJob.value}/result`)
    saveBlob(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }), `${props.run.run_id}-${s.node_id}.json`)
  } catch (err) { notifier.error(err, "The answer couldn't be downloaded") } finally { downloading.value = false }
}
</script>

<style scoped>
.axo-panel__child { padding: 10px 12px; border-radius: 10px; background: rgba(var(--v-theme-success), 0.06); border: 1px solid rgba(var(--v-theme-success), 0.2); }
.axo-panel__param { padding: 6px 0; border-bottom: 1px solid rgba(var(--v-border-color), 0.08); }
</style>
