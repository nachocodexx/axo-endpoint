<template>
  <v-dialog :model-value="modelValue" max-width="520" @update:model-value="emit('update:modelValue', $event)">
    <v-card rounded="lg">
      <v-card-title class="pt-5 px-6">Add endpoints</v-card-title>
      <v-card-text class="px-6">
        <p class="text-body-2 axo-muted mb-4">
          They start right away and the VE's target goes up to {{ present.length + count }}.
          Running more endpoints lets more jobs run at once.
        </p>
        <div class="text-body-2 mb-1">How many: <b>{{ count }}</b>
          <span class="axo-muted">({{ room }} more fit{{ room === 1 ? 's' : '' }} in the plan)</span></div>
        <v-slider v-model="count" :min="1" :max="Math.max(room, 1)" :step="1" :disabled="room < 1" show-ticks="always"
                  thumb-label hide-details class="mb-4" />
        <v-row density="compact">
          <v-col cols="6"><v-text-field v-model.number="cpu" type="number" step="0.5" min="0.25" label="CPU cores each" /></v-col>
          <v-col cols="6"><v-text-field v-model.number="mem" type="number" step="256" min="256" label="Memory (MB) each" /></v-col>
        </v-row>
        <div v-if="problem" class="text-caption text-error mb-2">{{ problem }}</div>
        <div v-else-if="limits.quota_cpu_cores" class="text-caption axo-muted mb-2">
          Uses {{ round(used.cpu + count * cpu) }} of {{ limits.quota_cpu_cores }} CPU ·
          {{ formatMemoryMb(used.mem + count * mem) }} of {{ formatMemoryMb(limits.quota_memory_mb) }} (the VE's quota).
        </div>
        <v-text-field v-model="reason" label="Why (optional, kept in the history)" hide-details class="mt-2" />
      </v-card-text>
      <v-card-actions class="px-6 pb-5">
        <v-spacer />
        <AxoBtn look="plain" variant="text" @click="emit('update:modelValue', false)">Cancel</AxoBtn>
        <AxoBtn :loading="saving" :disabled="room < 1 || !!problem" prepend-icon="mdi-plus" @click="save">
          Add {{ count }}
        </AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// Admin: "add N endpoints of this size" (POST /endpoints). Checked here
// against the plan's endpoint maximum and the VE's quota, as the VEM does.
const props = defineProps<{
  modelValue: boolean
  /** The VEM's GET /ve answer: settings + plan_limits. */
  view: { settings: Record<string, any>; plan_limits: Record<string, any> } | null
  present: EndpointRow[]
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'added', rows: EndpointRow[]): void }>()
const { api } = useCurrentVe()
const notify = useNotifier()

const count = ref(1)
const cpu = ref(1)
const mem = ref(1024)
const reason = ref('')
const limits = computed(() => props.view?.plan_limits ?? {})
const room = computed(() => Math.max(0, (limits.value.max_endpoints ?? 0) - props.present.length))
const used = computed(() => ({
  cpu: props.present.reduce((s, e) => s + (e.cpu_cores ?? 0), 0),
  mem: props.present.reduce((s, e) => s + (e.memory_mb ?? 0), 0),
}))
const round = (v: number) => Math.round(v * 100) / 100

watch(() => props.modelValue, (open) => {
  if (!open) return
  count.value = 1
  cpu.value = props.view?.settings.new_endpoint_cpu_cores ?? 1
  mem.value = props.view?.settings.new_endpoint_memory_mb ?? 1024
  reason.value = ''
})

const problem = computed(() => {
  if (!(cpu.value > 0) || !(mem.value > 0)) return 'Give each endpoint some CPU and memory.'
  const l = limits.value
  if (l.quota_cpu_cores == null) return null
  if (used.value.cpu + count.value * cpu.value > l.quota_cpu_cores + 1e-9
    || used.value.mem + count.value * mem.value > l.quota_memory_mb) {
    return `${count.value} more of ${cpu.value} CPU · ${formatMemoryMb(mem.value)} don't fit the quota of `
      + `${l.quota_cpu_cores} CPU · ${formatMemoryMb(l.quota_memory_mb)} (in use: ${round(used.value.cpu)} CPU · ${formatMemoryMb(used.value.mem)}).`
  }
  return null
})

const saving = ref(false)
async function save() {
  saving.value = true
  try {
    const rows = await api<EndpointRow[]>('/endpoints', {
      method: 'POST', body: { count: count.value, cpu_cores: cpu.value, memory_mb: mem.value, reason: reason.value || undefined },
    })
    notify.success(`Adding ${count.value} endpoint${count.value === 1 ? '' : 's'}`)
    emit('added', rows)
    emit('update:modelValue', false)
  } catch (err) {
    notify.error(err, "The endpoints couldn't be added")
  } finally {
    saving.value = false
  }
}
</script>
