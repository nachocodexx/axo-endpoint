<template>
  <div style="max-width: 760px">
    <template v-if="doc">
    <AxoPanel title="Settings" icon="mdi-tune-variant" help="Saving these makes a new version (the steps stay as they are). Runs already going keep the version they started with.">
      <div class="d-flex flex-column ga-4">
        <v-text-field v-model="form.name" density="compact" variant="outlined" label="Name" :error-messages="nameError" :disabled="!edit" hide-details="auto"
                      hint="Renaming keeps its runs and history." persistent-hint />
        <v-textarea v-model="form.description" density="compact" variant="outlined" label="Description" rows="2" auto-grow :disabled="!edit" hide-details />
        <div class="axo-ch-settings__grid">
          <v-text-field v-model.number="form.max_parallel_runs" type="number" :min="lim('max_parallel_runs').min" :max="lim('max_parallel_runs').max"
                        density="compact" variant="outlined" label="Runs at the same time" :disabled="!edit" :hint="`${hint('max_parallel_runs')} Default ${def('max_parallel_runs')}.`" persistent-hint />
          <v-text-field v-model.number="form.queue_limit" type="number" :min="lim('queue_limit').min" :max="lim('queue_limit').max"
                        density="compact" variant="outlined" label="Runs that may wait" :disabled="!edit" :hint="`${hint('queue_limit')} Default ${def('queue_limit')}.`" persistent-hint />
        </div>
        <v-switch v-model="form.allow_completed_with_errors" color="primary" inset density="compact" :disabled="!edit"
                  :label="form.allow_completed_with_errors ? 'Keep going after an error' : 'Stop the run at the first error'"
                  :hint="hint('allow_completed_with_errors')" persistent-hint />
        <v-select v-if="ends.length > 1" v-model="form.answer_step" :items="[{ value: null, title: 'Not picked' }, ...ends.map(e => ({ value: e, title: e }))]"
                  density="compact" variant="outlined" label="Its answer" :disabled="!edit" persistent-hint
                  :hint="hint('answer_step') || 'When another choreography runs this one as a step, the answer of this end step is what it gets.'"
                  :error-messages="!form.answer_step && doc.used_by?.length ? `${doc.used_by.map(u => u.name).join(', ')} call${doc.used_by.length === 1 ? 's' : ''} this one: pick the end step whose answer they get` : undefined" />
        <div v-else-if="ends.length === 1 && doc.used_by?.length" class="text-body-2 axo-muted">
          Its answer, when another choreography runs it as a step: the answer of its end step <span class="axo-mono">{{ ends[0] }}</span>.
        </div>
        <v-select v-model="form.keep_results" :items="[{ value: null, title: 'The VE default' }, ...KEEP_CHOICES]" density="compact" variant="outlined"
                  label="Keep step answers for" :disabled="!edit" :hint="hint('keep_results')" persistent-hint />
      </div>
      <div v-if="edit" class="d-flex align-center mt-5">
        <span v-if="dirty" class="text-caption axo-muted">Saving makes v{{ doc.latest_version + 1 }}.</span>
        <v-spacer />
        <v-btn v-if="dirty" variant="text" @click="reset">Undo changes</v-btn>
        <AxoBtn variant="flat" :disabled="!dirty || !!nameError || !valid" :loading="saving" @click="save">Save as v{{ doc.latest_version + 1 }}</AxoBtn>
      </div>
    </AxoPanel>

    <AxoPanel title="Sharing" icon="mdi-account-group-outline" class="mt-5">
      <VisibilityField kind="choreography" :ref-id="doc.choreography_id" :name="doc.name" />
    </AxoPanel>

    <AxoPanel v-if="can('admin')" title="Danger zone" icon="mdi-alert-outline" class="mt-5">
      <div class="d-flex align-center flex-wrap ga-3">
        <div class="text-body-2">Delete the choreography: its waiting and running runs are cancelled and its triggers stop.</div>
        <v-spacer />
        <AxoBtn look="danger" prepend-icon="mdi-delete-outline" @click="remove">Delete…</AxoBtn>
      </div>
    </AxoPanel>
    </template>
  </div>
</template>

<script setup lang="ts">
// Name, description, how many runs go / wait, keep going after an error,
// how long step answers are kept, which end step's answer is its answer
// when another choreography calls it (a new version when saved), who sees runs
// and results (at once), delete (Admin).
import type { Choreography } from '~/utils/choreographies'

const { id, can, api } = useCurrentVe()
const { doc, load } = useCurrentChoreography()
const { rows, load: loadList, info: designerInfo, designer } = useChoreographies()
const notifier = useNotifier()
const { confirm } = useConfirm()
const edit = computed(() => can('developer'))
onMounted(() => { designer().catch(() => null); loadList() })

const form = reactive({ name: '', description: '', max_parallel_runs: 10, queue_limit: 1000, allow_completed_with_errors: true, keep_results: null as string | null,
  answer_step: null as string | null })
const ends = computed(() => doc.value?.ends ?? [])
function reset() {
  const d = doc.value
  if (!d) return
  Object.assign(form, { name: d.name, description: d.description ?? '', ...d.settings, answer_step: d.settings.answer_step ?? null })
}
watch(() => doc.value?.version, reset, { immediate: true })
const dirty = computed(() => {
  const d = doc.value
  if (!d) return false
  return form.name !== d.name || form.description !== (d.description ?? '') || form.max_parallel_runs !== d.settings.max_parallel_runs
    || form.queue_limit !== d.settings.queue_limit || form.allow_completed_with_errors !== d.settings.allow_completed_with_errors
    || form.keep_results !== d.settings.keep_results || form.answer_step !== (d.settings.answer_step ?? null)
})
const setting = (n: string) => designerInfo.value?.choreography_settings.find(s => s.name === n)
const hint = (n: string) => setting(n)?.description ?? ''
const def = (n: string) => String(setting(n)?.default ?? (DEFAULT_SETTINGS as any)[n])
const lim = (n: string) => ({ min: setting(n)?.min ?? 0, max: setting(n)?.max ?? 100000 })
const valid = computed(() => form.max_parallel_runs >= lim('max_parallel_runs').min && form.max_parallel_runs <= lim('max_parallel_runs').max
  && form.queue_limit >= lim('queue_limit').min && form.queue_limit <= lim('queue_limit').max)
const nameError = computed(() => choreographyNameProblem(form.name)
  ?? (form.name !== doc.value?.name && rows.value.some(r => r.name === form.name) ? 'A choreography with this name already exists' : undefined))

const saving = ref(false)
async function save() {
  const d = doc.value
  if (!d) return
  saving.value = true
  try {
    const saved = await api<Choreography>(`/choreographies/${encodeURIComponent(d.choreography_id)}`, { method: 'PUT', body: {
      graph: cleanGraph({ ...d.graph, triggers: d.graph.triggers ?? d.triggers }), name: form.name, description: form.description || null,
      settings: { max_parallel_runs: form.max_parallel_runs, queue_limit: form.queue_limit, allow_completed_with_errors: form.allow_completed_with_errors, keep_results: form.keep_results,
        answer_step: form.answer_step } } })
    notifier.success(`Saved as v${saved.version}`)
    await loadList(true)
    if (saved.name !== d.name) await navigateTo(`/ves/${id.value}/choreographies/${encodeURIComponent(saved.name)}/settings`)
    else await load()
  } catch (err) { notifier.error(err, "The settings couldn't be saved") } finally { saving.value = false }
}
async function remove() {
  const d = doc.value
  if (!d) return
  if (!(await confirm({ title: `Delete ${d.name}?`, danger: true, typeToConfirm: d.name, confirmText: 'Delete' }))) return
  try {
    await api(`/choreographies/${encodeURIComponent(d.choreography_id)}`, { method: 'DELETE' })
    await loadList(true)
    notifier.success(`${d.name} deleted`)
    await navigateTo(`/ves/${id.value}/choreographies`)
  } catch (err) { notifier.error(err, `${d.name} couldn't be deleted`) }
}
</script>

<style scoped>
.axo-ch-settings__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
</style>
