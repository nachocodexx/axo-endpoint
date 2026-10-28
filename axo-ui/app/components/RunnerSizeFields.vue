<template>
  <div class="d-flex flex-wrap ga-2">
    <v-select :model-value="cpu" :items="cpuChoices" label="Runner CPU" density="compact" variant="outlined" hide-details
              class="axo-size" @update:model-value="emit('update:cpu', $event)" />
    <v-select :model-value="memoryMb" :items="memoryChoices" label="Runner memory" density="compact" variant="outlined" hide-details
              class="axo-size" @update:model-value="emit('update:memoryMb', $event)" />
    <div class="text-caption axo-muted w-100">
      Each runner of this function gets this much. Up to {{ fmtCpu(most.cpu) }} · {{ fmtMb(most.mb) }}: the {{ planName }} plan
      and the room in one endpoint ({{ fmtCpu(room.cpu) }} · {{ fmtMb(room.mb) }} after what the endpoint keeps for itself).
    </div>
  </div>
</template>

<script setup lang="ts">
// A container function's runner size: CPU and memory choices that fit both
// the plan's most and one endpoint's room (its size minus what it keeps for
// itself). null = the plan's default, which the VE fills in (shrunk to fit).
const props = defineProps<{ cpu: number | null; memoryMb: number | null }>()
const emit = defineEmits<{ 'update:cpu': [number | null]; 'update:memoryMb': [number | null] }>()

const { ve, api } = useCurrentVe()
const { apiFetch } = useApi()

// What an endpoint keeps for itself (the endpoint's AXO_ENDPOINT_RESERVED_* defaults).
const RESERVED = { cpu: 0.25, mb: 256 }
const CPUS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4]
const MEMORY = [128, 256, 512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192]

interface PlanRunner { name: string; runner_cpu_cores: number; runner_memory_mb: number; runner_max_cpu_cores: number;
  runner_max_memory_mb: number }
const plan = ref<PlanRunner | null>(null)
const endpoint = ref<{ cpu: number; mb: number } | null>(null)
onMounted(async () => {
  try {
    const answer = await apiFetch<{ plans: (PlanRunner & { plan_id: string })[] }>('/plans')
    plan.value = answer.plans.find(p => p.plan_id === ve.value?.plan_id) ?? null
  } catch { /* the choices fall back to the endpoint's room */ }
  try {
    const s = (await api<{ settings: { new_endpoint_cpu_cores: number; new_endpoint_memory_mb: number } }>('/ve')).settings
    endpoint.value = { cpu: s.new_endpoint_cpu_cores, mb: s.new_endpoint_memory_mb }
  } catch { /* unknown: only the plan limits */ }
})

const planName = computed(() => plan.value?.name ?? 'VE’s')
const room = computed(() => ({ cpu: (endpoint.value?.cpu ?? 4) - RESERVED.cpu, mb: (endpoint.value?.mb ?? 8192) - RESERVED.mb }))
const most = computed(() => ({
  cpu: Math.min(plan.value?.runner_max_cpu_cores ?? Infinity, room.value.cpu),
  mb: Math.min(plan.value?.runner_max_memory_mb ?? Infinity, room.value.mb),
}))
const defaults = computed(() => ({
  cpu: Math.min(plan.value?.runner_cpu_cores ?? 0.5, room.value.cpu),
  mb: Math.min(plan.value?.runner_memory_mb ?? 512, room.value.mb),
}))

const fmtCpu = (n: number) => `${n} CPU`
const fmtMb = (mb: number) => (mb >= 1024 ? `${mb / 1024} GB` : `${mb} MB`)
// A saved size the plan no longer allows still shows, so nothing changes silently.
const cpuChoices = computed(() => [
  { value: null, title: `Plan default (${fmtCpu(defaults.value.cpu)})` },
  ...[...new Set([...CPUS.filter(c => c <= most.value.cpu + 1e-9), ...(props.cpu != null ? [props.cpu] : [])])]
    .sort((a, b) => a - b).map(c => ({ value: c, title: fmtCpu(c) })),
])
const memoryChoices = computed(() => [
  { value: null, title: `Plan default (${fmtMb(defaults.value.mb)})` },
  ...[...new Set([...MEMORY.filter(m => m <= most.value.mb), ...(props.memoryMb != null ? [props.memoryMb] : [])])]
    .sort((a, b) => a - b).map(m => ({ value: m, title: fmtMb(m) })),
])
</script>

<style scoped>
.axo-size { flex: 1 1 220px; }
</style>
