<template>
  <v-dialog :model-value="modelValue" max-width="560" @update:model-value="emit('update:modelValue', $event)">
    <v-card rounded="lg">
      <v-card-title class="pt-5 px-6">Change plan or quota</v-card-title>
      <v-card-text class="px-6">
        <AxoErrorAlert v-if="loadError" :error="loadError" title="The plans couldn't load" :retry="load" class="mb-3" />
        <v-select v-model="planId" :items="planItems" label="Plan" item-props class="mb-2" />
        <v-row v-if="plan && !plan.fixed_endpoint_size" density="compact">
          <v-col cols="6"><v-text-field v-model.number="cpu" type="number" step="0.5" min="0.25" label="Quota CPU cores" /></v-col>
          <v-col cols="6"><v-text-field v-model.number="mem" type="number" step="256" min="256" label="Quota memory (MB)" /></v-col>
        </v-row>
        <v-alert v-if="plan && planId !== ve.plan_id" :type="upgrade ? 'info' : 'warning'" variant="tonal" density="compact">
          <template v-if="upgrade">
            Applies now. You pay the difference for this period: about
            <b>{{ formatMoney(plan.monthly_fee_cents - (ve.rental.period_fee_cents ?? 0)) }}</b>.
          </template>
          <template v-else>
            Applies at the next renewal ({{ formatDate(ve.rental.period_end) }}). Endpoints over the
            {{ plan.name }} limit ({{ plan.max_endpoints }}) are removed then, and history older than
            {{ plan.max_retention_days }} days is hidden.
          </template>
        </v-alert>
        <div v-if="ve.rental.pending_change" class="text-body-2 axo-muted mt-2">
          Already planned for {{ formatDate(ve.rental.period_end) }}: the
          {{ plans.find(p => p.plan_id === ve.rental.pending_change!.plan_id)?.name ?? ve.rental.pending_change.plan_id }} plan,
          quota {{ ve.rental.pending_change.quota?.cpu_cores }} CPU · {{ formatMemoryMb(ve.rental.pending_change.quota?.memory_mb) }}.
        </div>
      </v-card-text>
      <v-card-actions class="px-6 pb-5">
        <v-spacer />
        <AxoBtn look="plain" variant="text" @click="emit('update:modelValue', false)">Close</AxoBtn>
        <AxoBtn :loading="saving" :disabled="!changed" @click="save">Change</AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { VeCard } from 'axo-ui-kit/app/types/cp'

// Owner: another plan (an upgrade now, with the fee difference; a downgrade
// at the next renewal) and/or another quota (PATCH /ves/{id}/plan).
const props = defineProps<{ modelValue: boolean; ve: VeCard }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'changed'): void }>()
const { apiFetch } = useApi()
const notify = useNotifier()

const plans = ref<any[]>([])
const loadError = ref<unknown>(null)
const planId = ref(props.ve.plan_id)
const cpu = ref(props.ve.quota.cpu_cores)
const mem = ref(props.ve.quota.memory_mb)

async function load() {
  loadError.value = null
  try { plans.value = (await apiFetch<{ plans: any[] }>('/plans')).plans.filter(p => p.active).sort((a, b) => a.order - b.order) }
  catch (err) { loadError.value = err }
}
watch(() => props.modelValue, (open) => {
  if (!open) return
  planId.value = props.ve.plan_id
  cpu.value = props.ve.quota.cpu_cores
  mem.value = props.ve.quota.memory_mb
  if (!plans.value.length) load()
})

const plan = computed(() => plans.value.find(p => p.plan_id === planId.value))
const current = computed(() => plans.value.find(p => p.plan_id === props.ve.plan_id))
// Same rule as the Control Plane: a lower fee waits for the renewal.
const upgrade = computed(() => (plan.value?.monthly_fee_cents ?? 0) >= (props.ve.rental.period_fee_cents ?? current.value?.monthly_fee_cents ?? 0))
const planItems = computed(() => plans.value.map(p => ({
  title: p.name, value: p.plan_id,
  subtitle: `${formatMoney(p.monthly_fee_cents, { free: true })} / month · up to ${p.max_endpoints} endpoints`,
  disabled: p.personal_only && props.ve.owner.kind === 'team',
})))
const changed = computed(() => planId.value !== props.ve.plan_id
  || cpu.value !== props.ve.quota.cpu_cores || mem.value !== props.ve.quota.memory_mb)

const saving = ref(false)
async function save() {
  saving.value = true
  try {
    const answer = await apiFetch<{ applies: 'now' | 'at_renewal' }>(`/ves/${props.ve.ve_id}/plan`, {
      method: 'PATCH',
      body: {
        plan_id: planId.value !== props.ve.plan_id ? planId.value : undefined,
        quota_cpu_cores: cpu.value !== props.ve.quota.cpu_cores ? cpu.value : undefined,
        quota_memory_mb: mem.value !== props.ve.quota.memory_mb ? mem.value : undefined,
      },
    })
    notify.success(answer.applies === 'now' ? 'Plan changed' : 'Change planned for the next renewal')
    emit('changed')
    emit('update:modelValue', false)
  } catch (err) {
    notify.error(err, "The plan couldn't be changed")
  } finally {
    saving.value = false
  }
}
</script>
