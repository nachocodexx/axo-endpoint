<template>
  <div>
    <v-breadcrumbs :items="crumbs" density="compact" class="px-0 pt-0 mb-1" />

    <AxoErrorAlert v-if="error" :error="error" :title="notFound ? 'This job is not in the VE' : 'The job couldn\'t load'"
                   :retry="notFound ? undefined : load" class="mb-4" />
    <v-alert v-if="!vemUp" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: the job shows here once it runs.
    </v-alert>
    <AxoSkeleton v-if="vemUp && !job && !error" :lines="10" />

    <template v-if="job">
      <div class="d-flex align-center flex-wrap ga-2 mb-1">
        <v-icon color="primary">mdi-briefcase-outline</v-icon>
        <span class="text-h6 text-truncate">{{ jobSubject(job) }}</span>
        <span class="text-body-2 axo-muted">v{{ job.function_version }}</span>
        <AxoStatusChip kind="job" :value="job.status" />
        <v-spacer />
        <AxoBtn v-if="actions.canCancel(job)" look="danger" prepend-icon="mdi-cancel" :loading="actions.busy.value === `cancel:${job.job_id}`"
                @click="act(actions.cancel(job))">Cancel</AxoBtn>
        <AxoBtn v-if="actions.canRetry(job)" look="plain" prepend-icon="mdi-refresh" :loading="actions.busy.value === `retry:${job.job_id}`"
                @click="act(actions.retry(job))">Retry</AxoBtn>
        <AxoBtn v-if="can('developer') && job.kind === 'function'" look="plain" prepend-icon="mdi-play-outline" @click="actions.runAgain(job)">
          Run again
        </AxoBtn>
        <AxoBtn v-if="actions.hasResult(job)" look="plain" prepend-icon="mdi-download" :loading="actions.busy.value === `download:${job.job_id}`"
                @click="actions.downloadResult(job)">Download result</AxoBtn>
      </div>
      <div class="d-flex align-center flex-wrap ga-2 mb-4 text-body-2">
        <AxoCopy :text="job.job_id" max-width="320px" />
        <span class="axo-muted">·</span>
        <span class="d-inline-flex align-center ga-1">
          <v-icon size="16">{{ STARTED_BY_ICONS[job.started_by_type ?? 'user'] ?? 'mdi-account-outline' }}</v-icon>{{ startedByText(job) }}
        </span>
        <span class="axo-muted">·</span>
        <span :title="formatExactTime(ts(job.created_at))">received {{ timeAgo(ts(job.created_at)) }}</span>
        <template v-if="totalMs != null"><span class="axo-muted">·</span><span>{{ isActive(job) ? 'so far' : 'took' }} {{ formatDuration(totalMs) }}</span></template>
        <template v-if="cost != null"><span class="axo-muted">·</span><span>cost {{ formatUcents(cost) }}</span></template>
      </div>

      <v-alert :type="summaryTone" variant="tonal" density="compact" class="mb-4" :icon="false">
        <div class="d-flex align-center flex-wrap ga-2">
          <span class="text-body-1">{{ summaryText }}</span>
          <AxoErrorName v-if="job.error?.name && !isActive(job) && job.status !== 'COMPLETED'" :name="job.error.name"
                        :code="job.error.code ?? undefined" />
        </div>
        <div v-if="job.error?.message && job.status === 'FAILED'" class="text-body-2 mt-1">{{ job.error.message }}</div>
      </v-alert>

      <v-row density="compact">
        <v-col cols="12" lg="7">
          <div class="text-subtitle-1 font-weight-medium mb-2 d-flex align-center ga-1">
            Attempts
            <AxoHelp title="Attempts" text="Every try of this job, newest first. A failure of the platform is tried again on its own (free); a failure of your code only as many times as the job's attempts allow. Retry adds one more." />
          </div>
          <div class="d-flex flex-column ga-3 mb-4">
            <v-card v-if="pending" variant="outlined" class="pa-3 d-flex align-center ga-2">
              <v-progress-circular indeterminate size="18" width="2" color="info" />
              <span class="text-body-1 font-weight-medium">Attempt {{ job.attempt }}</span>
              <span class="text-body-2">{{ pending }}</span>
            </v-card>
            <JobAttemptCard v-for="a in attemptsNewestFirst" :key="a.attempt" :attempt="a" :cost="attemptCosts[a.attempt] ?? null"
                            :start-open="a.attempt === meaningful" :can-see-traceback="can('developer')" />
            <AxoEmptyState v-if="!attemptsNewestFirst.length && !pending" icon="mdi-timer-sand" title="No attempt finished yet" />
          </div>
        </v-col>

        <v-col cols="12" lg="5">
          <AxoPanel title="Result" icon="mdi-code-json" stacked class="mb-3"
                    help="What the function returned (always JSON). Kept in the VE's results bucket for the time the job asked.">
            <template #actions>
              <template v-if="result">
                <v-btn size="small" variant="text" icon="mdi-content-copy" aria-label="Copy the result" @click="copy(result.text)" />
                <v-btn size="small" variant="text" icon="mdi-fullscreen" aria-label="Open in full page" @click="previewResult" />
              </template>
            </template>
            <div v-if="job.status === 'COMPLETED'">
              <div class="text-caption axo-muted mb-2">
                <template v-if="job.result?.deleted_at">Result deleted on {{ formatDateTime(ts(job.result.deleted_at)) }}.</template>
                <template v-else-if="job.result?.stored === false">Not kept: {{ job.result.reason }}</template>
                <template v-else-if="job.result?.stored">
                  Kept in this VE's results bucket · {{ formatBytes(job.result.size ?? result?.size) }} ·
                  {{ job.keep_until ? `deleted on ${formatDateTime(ts(job.keep_until))}` : 'never deleted' }}
                </template>
                <template v-else>Saving the result…</template>
              </div>
              <AxoErrorAlert v-if="resultError" :error="resultError" title="The result couldn't load" :retry="loadResult" />
              <template v-else-if="result">
                <AxoCode :model-value="result.text" language="json" readonly min-height="60px" max-height="420px" label="Result" />
                <div v-if="result.cut" class="text-caption axo-muted mt-1">It's long: the start is shown. Download it for all of it.</div>
              </template>
              <v-skeleton-loader v-else-if="!job.result?.deleted_at && job.result?.stored !== false" type="paragraph" />
            </div>
            <div v-else class="text-body-2 axo-muted">
              {{ isActive(job) ? 'The result shows here when the job finishes.' : 'Only a job that succeeded has a result.' }}
            </div>
          </AxoPanel>

          <AxoPanel v-if="files.length" title="Files written" icon="mdi-file-multiple-outline" stacked flush class="mb-3"
                    help="Files the job wrote to buckets. Open one to see it (your access to the bucket is checked).">
            <v-list density="compact" class="bg-transparent py-0">
              <v-list-item v-for="(f, i) in files" :key="`${f.bucket}/${f.name}@${f.version}`" :title="f.name"
                           :subtitle="`${f.bucket} · v${f.version ?? '—'} · ${formatBytes(f.size)}`" @click="openFile(i)">
                <template #prepend><v-icon>{{ fileIcon(f.name) }}</v-icon></template>
                <template #append>
                  <v-btn icon="mdi-download" size="small" variant="text" :aria-label="`Download ${f.name}`" @click.stop="bucketFiles.download(f)" />
                </template>
              </v-list-item>
            </v-list>
          </AxoPanel>

          <AxoPanel title="Parameters" icon="mdi-tune-variant" stacked class="mb-3">
            <template #actions>
              <v-btn size="small" variant="text" icon="mdi-content-copy" aria-label="Copy the parameters" @click="copy(paramsText)" />
            </template>
            <AxoCode :model-value="paramsText" language="json" readonly min-height="40px" max-height="300px" label="Parameters" />
            <AxoBtn v-if="can('developer') && job.kind === 'function'" look="plain" variant="text" size="small" class="mt-2"
                    prepend-icon="mdi-play-outline" @click="actions.runAgain(job)">Run again with these</AxoBtn>
          </AxoPanel>

          <AxoPanel title="Details" icon="mdi-information-outline" stacked>
            <AxoKeyValue :items="facts" :columns="2" />
          </AxoPanel>
        </v-col>
      </v-row>
    </template>

    <AxoFilePreview v-model="previewing" v-model:index="previewIndex" :files="previewFiles" />
  </div>
</template>

<script setup lang="ts">
import type { JobDetail } from '~/utils/jobs'
import type { PreviewFile } from 'axo-ui-kit/app/components/AxoFilePreview.vue'

// One job: who started it and how it went, every attempt (newest first,
// with its time bar and error), its result (JSON), the files it wrote
// (previewed full page), its parameters. Live while it runs. Developers
// cancel / retry / run again; Owners and Admins see the cost.
const { id, ve, can, cp, api, vemUp } = useCurrentVe()
const route = useRoute()
const jobId = computed(() => String(route.params.jobId))
const actions = useJobActions()
const bucketFiles = useBucketFiles()
useHead(() => ({ title: `Job ${jobId.value.slice(0, 8)} · ${ve.value?.name ?? 'VE'} · axo` }))

const job = ref<JobDetail | null>(null)
const error = ref<unknown>(null)
const notFound = computed(() => (error.value as any)?.status === 404)

async function load() {
  if (!vemUp.value) return
  try {
    const fresh = await api<JobDetail>(`/jobs/${jobId.value}`)
    const finishedNow = job.value && isActive(job.value) && !isActive(fresh)
    job.value = fresh
    error.value = null
    if (fresh.status === 'COMPLETED' && (!result.value || finishedNow)) loadResult()
    if (!isActive(fresh)) loadCosts()
  } catch (err) {
    error.value = err
  }
}
watch([jobId, vemUp], () => { job.value = null; result.value = null; load() }, { immediate: true })
usePolling(() => (job.value && !isActive(job.value) && job.value.result ? undefined : load()), 3000)
useVeLive(() => (vemUp.value ? `jobs/${jobId.value}` : null), () => load())

// ── result ─────────────────────────────────────────────────────────────────
const result = ref<{ text: string; cut: boolean; size: number; value: unknown } | null>(null)
const resultError = ref<unknown>(null)
async function loadResult() {
  resultError.value = null
  if (job.value?.result?.deleted_at || job.value?.result?.stored === false) return
  try {
    const value = await api(`/jobs/${jobId.value}/result`)
    result.value = { ...prettyJson(value), value }
  } catch (err) {
    if ((err as any)?.errorName !== 'JOB_RESULT_NOT_KEPT' || job.value?.result?.stored) resultError.value = err
  }
}

// ── costs (Owner / Admin) ──────────────────────────────────────────────────
const cost = ref<number | null>(null)
const attemptCosts = ref<Record<number, number>>({})
async function loadCosts() {
  if (!can('admin') || cost.value != null) return
  try {
    const answer = await cp<{ jobs: Record<string, { cost_ucents: number }>; attempts?: Record<string, { cost_ucents: number }> }>(
      `/costs/jobs?job_ids=${jobId.value}`)
    cost.value = answer.jobs[jobId.value]?.cost_ucents ?? null
    attemptCosts.value = Object.fromEntries(Object.entries(answer.attempts ?? {}).map(([k, v]) => [Number(k), v.cost_ucents]))
  } catch { /* the billing record arrives a little later: no cost shown yet */ }
}

// ── what's shown ───────────────────────────────────────────────────────────
const attemptsNewestFirst = computed(() => [...(job.value?.attempts ?? [])].sort((a, b) => b.attempt - a.attempt))
/** The attempt that tells the story: the one that succeeded, else the last. */
const meaningful = computed(() => attemptsNewestFirst.value.find(a => a.outcome === 'completed')?.attempt
  ?? attemptsNewestFirst.value[0]?.attempt)
const pending = computed(() => {
  const j = job.value
  if (!j || !isActive(j) || j.attempts.some(a => a.attempt === j.attempt)) return null
  return j.status === 'RUNNING' ? 'Running…' : j.status === 'WAITING_RETRY' ? 'Waiting to try again' : 'Waiting for a free runner…'
})
const totalMs = computed(() => {
  const j = job.value
  if (!j) return null
  if (!isActive(j)) return j.duration_ms ?? null
  return j.created_at ? Date.now() - Date.parse(j.created_at) : null
})
const summaryTone = computed(() => ({ COMPLETED: 'success', FAILED: 'error', CANCELLED: 'info', WAITING_RETRY: 'warning' } as Record<string, any>)[job.value?.status ?? ''] ?? 'info')
const summaryText = computed(() => {
  const j = job.value!
  const of = Math.max(j.max_attempts, j.attempt)
  const on = of > 1 ? ` on attempt ${j.attempt} of ${of}` : ''
  switch (j.status) {
    case 'COMPLETED': return `Succeeded${on}.`
    case 'FAILED': return `Failed${on}${j.error?.fault ? `: ${FAULTS[j.error.fault]!.label.toLowerCase()}` : ''}.`
    case 'CANCELLED': return `Cancelled${j.cancelled_by ? ' by a person' : ''}${on}.`
    case 'WAITING_RETRY': return `Attempt ${j.attempt} failed; trying again ${j.next_attempt_at ? timeAgo(ts(j.next_attempt_at)) : 'soon'}.`
    case 'RUNNING': return `Running${on}.`
    default: return `Waiting for a free runner${on}.`
  }
})
const paramsText = computed(() => JSON.stringify(job.value?.params ?? {}, null, 2))
const files = computed(() => {
  const all = job.value?.attempts.flatMap(a => a.files_written ?? []) ?? []
  const seen = new Set<string>()
  return all.filter((f) => { const k = `${f.bucket}/${f.name}@${f.version}`; if (seen.has(k)) return false; seen.add(k); return true })
})
const facts = computed(() => {
  const j = job.value!
  return [
    j.kind === 'object'
      ? { label: 'Object', value: `${jobSubject(j)} · class v${j.function_version}`, to: j.object_name ? `/ves/${id.value}/objects/${encodeURIComponent(j.object_name)}` : undefined }
      : { label: 'Function', value: `${jobSubject(j)} v${j.function_version}` },
    { label: 'Endpoint', value: j.endpoint_id ?? '—', mono: true },
    { label: 'Attempts allowed', value: String(Math.max(j.max_attempts, 1)) },
    { label: 'Result kept for', value: keepTitle(j.keep_result) },
    { label: 'Started', value: j.started_at ? formatExactTime(ts(j.started_at)) : '—' },
    { label: 'Finished', value: j.finished_at ? formatExactTime(ts(j.finished_at)) : '—' },
    ...(j.warnings?.length ? [{ label: 'Warnings', value: j.warnings.join(' · ') }] : []),
  ]
})
const crumbs = computed(() => {
  const j = job.value
  const base = [{ title: 'Jobs', to: `/ves/${id.value}/jobs` }]
  if (j?.choreography) {
    base.push({ title: `${j.choreography.choreography_name ?? 'Choreography'} · run`, to: `/ves/${id.value}/choreographies/${encodeURIComponent(j.choreography.choreography_name ?? j.choreography.choreography_id)}/runs/${j.choreography.run_id}` })
    base.push({ title: `Step ${j.choreography.step}`, to: `/ves/${id.value}/choreographies/${encodeURIComponent(j.choreography.choreography_name ?? j.choreography.choreography_id)}/runs/${j.choreography.run_id}?step=${encodeURIComponent(j.choreography.step)}` })
  }
  base.push({ title: jobId.value.slice(0, 12), to: undefined as any })
  return base.map(c => ({ ...c, disabled: !c.to }))
})

// ── files ──────────────────────────────────────────────────────────────────
const previewing = ref(false)
const previewIndex = ref(0)
const previewFiles = ref<PreviewFile[]>([])
function openFile(i: number) {
  previewFiles.value = bucketFiles.previewOf(files.value)
  previewIndex.value = i
  previewing.value = true
}
function previewResult() {
  const value = result.value
  if (!value) return
  const blob = () => Promise.resolve(new Blob([JSON.stringify(value.value, null, 2)], { type: 'application/json' }))
  previewFiles.value = [{ key: 'result', name: `${jobId.value}.json`, subtitle: 'Result', size: value.size,
    load: blob, download: () => actions.downloadResult(job.value!) }]
  previewIndex.value = 0
  previewing.value = true
}
const fileIcon = (name: string) => {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return 'mdi-file-image-outline'
  if (ext === 'pdf') return 'mdi-file-pdf-box'
  if (['csv', 'tsv'].includes(ext)) return 'mdi-file-table-outline'
  if (ext === 'json') return 'mdi-code-json'
  return 'mdi-file-outline'
}

async function act(done: Promise<boolean>) { if (await done) load() }
async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); useNotifier().success('Copied') } catch { useNotifier().warning("Couldn't copy") }
}
</script>
