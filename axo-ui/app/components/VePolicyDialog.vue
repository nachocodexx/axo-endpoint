<template>
  <v-dialog :model-value="modelValue" max-width="720" scrollable @update:model-value="emit('update:modelValue', $event)">
    <v-card rounded="lg">
      <v-card-title class="pt-5 px-6">{{ policy?.policy_id ? `Change ${policy.name}` : 'New scaling policy' }}</v-card-title>
      <v-card-text class="px-6">
        <v-form ref="formRef" v-model="valid">
          <template v-if="!policy?.policy_id">
            <div class="text-body-2 mb-2">Start from</div>
            <v-chip-group v-model="templateName" mandatory selected-class="text-primary" column class="mb-3">
              <v-chip v-for="t in templates" :key="t.template" :value="t.template" variant="outlined" filter size="small">
                {{ t.template.replaceAll('-', ' ') }}
              </v-chip>
            </v-chip-group>
            <div v-if="template" class="text-body-2 axo-muted mb-4">{{ template.description }}</div>
          </template>

          <v-text-field v-model="p.name" label="Name" :rules="[v => !!v || 'A name is needed']" class="mb-2" />

          <div class="text-overline">When</div>
          <v-row density="compact">
            <v-col cols="6" sm="3"><v-select v-model="p.statistic" :items="STATISTICS" label="Statistic" /></v-col>
            <v-col cols="12" sm="5"><v-select v-model="p.metric" :items="METRICS" label="of" /></v-col>
            <v-col cols="3" sm="2"><v-select v-model="p.comparator" :items="['>', '<', '>=', '<=']" label="is" /></v-col>
            <v-col cols="9" sm="2"><v-text-field v-model.number="p.threshold" type="number" :label="unitOf(p.metric)" /></v-col>
          </v-row>
          <v-row density="compact">
            <v-col cols="6"><v-text-field v-model.number="p.duration_s" type="number" min="0" label="for at least (seconds)" /></v-col>
            <v-col cols="6"><v-text-field v-model.number="p.window_s" type="number" min="1" label="measured over the last (seconds)" /></v-col>
          </v-row>

          <div class="text-overline">Then</div>
          <v-row density="compact">
            <v-col cols="12" sm="6">
              <v-select v-model="p.kind" :items="KINDS" label="Change" :disabled="!!policy?.policy_id" />
            </v-col>
            <v-col cols="12" sm="6">
              <v-select v-model="p.delta" :items="DELTAS" label="by" />
            </v-col>
            <v-col v-if="p.kind === 'runner'" cols="12">
              <v-select v-model="fnKey" :items="functionItems" label="Function" :rules="[v => !!v || 'Pick a function']" />
            </v-col>
          </v-row>

          <div class="text-overline">Limits</div>
          <v-row density="compact">
            <v-col cols="4"><v-text-field v-model.number="p.min_value" type="number" :min="p.kind === 'runner' ? 1 : 0" label="Never below" /></v-col>
            <v-col cols="4"><v-text-field v-model.number="p.max_value" type="number" :min="p.min_value" :max="p.kind === 'endpoint' ? maxEndpoints : undefined" label="Never above"
                                          :rules="[v => v >= p.min_value || 'At least the minimum']" /></v-col>
            <v-col cols="4"><v-text-field v-model.number="p.cooldown_s" type="number" min="0" label="Then wait (seconds)" /></v-col>
          </v-row>
          <v-switch v-model="p.dry_run" color="primary" inset hide-details label="Dry run: only record what it would do" />
          <v-alert type="info" variant="tonal" density="compact" class="mt-3 text-body-2">
            {{ sentence }}
          </v-alert>
        </v-form>
      </v-card-text>
      <v-card-actions class="px-6 pb-5">
        <v-spacer />
        <AxoBtn look="plain" variant="text" @click="emit('update:modelValue', false)">Close</AxoBtn>
        <AxoBtn :loading="saving" :disabled="!valid" @click="save">{{ policy?.policy_id ? 'Save' : 'Create' }}</AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// A scaling policy: WHEN <statistic> of <metric> <comparator> <threshold>
// for <duration>, THEN target ± delta, within min / max, then a cooldown.
// New ones start from one of the VEM's templates.
const props = defineProps<{ modelValue: boolean; policy?: any; templates: any[]; maxEndpoints: number }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved'): void }>()
const { api } = useCurrentVe()
const notify = useNotifier()

const METRICS = [
  { title: 'Job time (ms)', value: 'job.total_ms' }, { title: 'Wait time (ms)', value: 'job.wait_ms' },
  { title: 'Run time (ms)', value: 'job.exec_ms' }, { title: 'Cold start (ms)', value: 'job.cold_start_ms' },
  { title: 'Queue length (jobs)', value: 'queue.length' }, { title: 'Queue fullness (%)', value: 'queue.fullness_pct' },
  { title: 'Requests received', value: 'requests.received' }, { title: 'Requests turned away', value: 'requests.rejected' },
  { title: 'Requests that waited too long', value: 'requests.timed_out' }, { title: 'Requests retried', value: 'requests.retried' },
  { title: 'CPU (%)', value: 'resource.cpu_pct' }, { title: 'Memory (%)', value: 'resource.ram_pct' },
]
const STATISTICS = ['avg', 'median', 'p90', 'p95', 'p99', 'min', 'max', 'total', 'count']
const KINDS = [{ title: 'the number of endpoints', value: 'endpoint' }, { title: "a function's runners", value: 'runner' }]
const DELTAS = [{ title: '+1 (add one)', value: 1 }, { title: '+2', value: 2 }, { title: '−1 (remove one)', value: -1 }, { title: '−2', value: -2 }]
const unitOf = (m: string) => (m.endsWith('_ms') ? 'ms' : m.endsWith('_pct') ? '%' : 'value')

const p = ref<Record<string, any>>({})
const templateName = ref<string | null>(null)
const template = computed(() => props.templates.find(t => t.template === templateName.value))
const functions = ref<Array<{ function_id: string; name: string; version: number }>>([])
const fnKey = ref<string | null>(null)
const functionItems = computed(() => functions.value.map(f => ({ title: `${f.name} v${f.version}`, value: `${f.function_id}:${f.version}` })))
const valid = ref(true)

watch(() => props.modelValue, async (open) => {
  if (!open) return
  if (props.policy) {
    p.value = { ...props.policy }
    fnKey.value = props.policy.function_id ? `${props.policy.function_id}:${props.policy.function_version}` : null
  } else {
    templateName.value = props.templates[0]?.template ?? null
    fromTemplate()
  }
  try { functions.value = await api('/functions') } catch { functions.value = [] }
})
watch(templateName, () => { if (!props.policy) fromTemplate() })
function fromTemplate() {
  const t = template.value
  p.value = { name: t?.template ?? '', cooldown_s: 300, window_s: 60, dry_run: false, ...(t?.policy ?? {}) }
  p.value.max_value = Math.min(p.value.max_value ?? 1, p.value.kind === 'endpoint' ? props.maxEndpoints : p.value.max_value ?? 1)
}

const metricTitle = (m: string) => METRICS.find(x => x.value === m)?.title.replace(/ \(.*\)$/, '').toLowerCase() ?? m
const sentence = computed(() => {
  const x = p.value
  const what = x.kind === 'runner' ? `the function's runners` : 'endpoints'
  const act = x.delta > 0 ? `add ${x.delta}` : `remove ${-x.delta}`
  return `When the ${x.statistic} ${metricTitle(x.metric ?? '')} over the last ${x.window_s} s is ${x.comparator} ${x.threshold} for ${x.duration_s} s, `
    + `${act} ${what} (staying between ${x.min_value} and ${x.max_value}), then wait ${x.cooldown_s} s.${x.dry_run ? ' Dry run: nothing changes, the decision is only recorded.' : ''}`
})

const saving = ref(false)
async function save() {
  saving.value = true
  const body: Record<string, any> = { ...p.value }
  delete body.condition
  if (body.kind === 'runner' && fnKey.value) {
    const [fid, version] = fnKey.value.split(':')
    body.function_id = fid
    body.function_version = Number(version)
  }
  try {
    if (props.policy?.policy_id) await api(`/policies/${props.policy.policy_id}`, { method: 'PATCH', body })
    else await api('/policies', { method: 'POST', body })
    notify.success(props.policy?.policy_id ? 'Policy saved' : 'Policy created')
    emit('saved')
    emit('update:modelValue', false)
  } catch (err) {
    notify.error(err, "The policy couldn't be saved")
  } finally {
    saving.value = false
  }
}
</script>
