<template>
  <div>
    <AxoErrorAlert v-if="error" :error="error" title="This VE couldn't load" :retry="load" class="mb-4" />

    <template v-if="ve">
      <AxoPageHeader :title="ve.name" icon="mdi-cube-outline" :crumbs="[{ title: 'VEs', to: '/ves' }, { title: ve.name }]">
        <template #title-append>
          <AxoStatusChip :value="ve.lifecycle" :reason="ve.lifecycle_reason" class="ml-2" />
          <AxoStatusChip v-if="ve.health" kind="health" :value="ve.health" class="ml-1" />
        </template>
        <template #subtitle>
          {{ planName(ve.plan_id) }} plan · {{ ve.endpoints_running }} of {{ ve.target_endpoints }} endpoint{{ ve.target_endpoints === 1 ? '' : 's' }} running
          · you are {{ roleTitle(role) }}
        </template>
        <template #actions>
          <AxoBtn v-if="can('admin') && canStart" prepend-icon="mdi-play" :loading="busy === 'start'" @click="start">Start</AxoBtn>
          <AxoBtn v-if="can('admin') && canStop" look="plain" prepend-icon="mdi-stop" :loading="busy === 'stop'" @click="stop">Stop</AxoBtn>
          <v-menu v-if="can('admin')" location="bottom end" @update:model-value="$event && loadRepair()">
            <template #activator="{ props: menu }">
              <v-btn v-bind="menu" icon="mdi-dots-vertical" variant="text" aria-label="More actions" />
            </template>
            <v-list density="compact" rounded="lg" min-width="260">
              <v-list-item prepend-icon="mdi-wrench-outline" title="Repair" :disabled="!canRepair || repairWait > 0"
                           :subtitle="repairSubtitle" @click="repairDialog = true" />
              <template v-if="can('owner')">
                <v-divider class="my-1" />
                <v-list-item prepend-icon="mdi-swap-vertical" title="Change plan or quota" @click="planDialog = true" />
                <v-list-item v-if="ve.rental.cancel_at_period_end || ve.rental.ended_at" prepend-icon="mdi-restore"
                             title="Keep renting" @click="resume" />
                <v-list-item v-else-if="ve.rental.period_fee_cents" prepend-icon="mdi-calendar-remove-outline"
                             title="Cancel the rental" @click="cancel" />
                <v-divider class="my-1" />
                <v-list-item prepend-icon="mdi-delete-outline" title="Delete this VE" base-color="error" @click="remove" />
              </template>
            </v-list>
          </v-menu>
        </template>
      </AxoPageHeader>

      <v-alert v-if="ve.rental.cancel_at_period_end" type="warning" variant="tonal" density="compact" class="mb-4"
               icon="mdi-calendar-remove-outline">
        Cancelled: it keeps working until {{ formatDateTime(ve.rental.period_end) }}, then stops.
        <template v-if="can('owner')" #append><v-btn size="small" variant="text" @click="resume">Keep renting</v-btn></template>
      </v-alert>
      <v-alert v-if="ve.lifecycle === 'Failed'" type="error" variant="tonal" density="compact" class="mb-4">
        {{ ve.lifecycle_reason || 'Something went wrong while changing this VE.' }}
      </v-alert>
      <v-alert v-else-if="canRepair && ['Unavailable', 'Degraded'].includes(ve.health ?? '')"
               :type="ve.health === 'Unavailable' ? 'error' : 'warning'" variant="tonal" density="compact" class="mb-4"
               icon="mdi-heart-broken-outline">
        {{ ve.health === 'Unavailable'
          ? `${ve.name} isn't working right now${ve.lifecycle_reason ? `: ${ve.lifecycle_reason}` : ''}. The platform repairs it on its own after 10 minutes.`
          : `${ve.name} works with fewer endpoints than it should${ve.lifecycle_reason ? ` (${ve.lifecycle_reason})` : ''}.` }}
        <template v-if="can('admin')" #append>
          <v-btn size="small" variant="text" class="text-none" prepend-icon="mdi-wrench-outline" :disabled="repairWait > 0"
                 @click="repairDialog = true">{{ repairWait > 0 ? `Repair (in ${Math.ceil(repairWait / 60)} min)` : 'Repair' }}</v-btn>
        </template>
      </v-alert>

      <v-tabs v-if="onTabs" :model-value="tab" color="primary" class="mb-6 axo-ve-tabs" show-arrows>
        <v-tab v-for="t in tabs" :key="t.path" :value="t.path" :to="`/ves/${id}${t.path ? `/${t.path}` : ''}`" :exact="!t.path" :prepend-icon="t.icon">
          {{ t.title }}
        </v-tab>
      </v-tabs>

      <NuxtPage />
      <VePlanDialog v-model="planDialog" :ve="ve" @changed="load" />
      <VeRepairDialog v-model="repairDialog" :ve="ve" @repaired="afterRepair" />
    </template>
    <AxoSkeleton v-else-if="!error" :lines="8" />
  </div>
</template>

<script setup lang="ts">
// The frame of every page of one VE: name, status, plan, the lifecycle
// actions (Admin: start / stop / repair; Owner: plan, rental, delete), a
// banner with Repair while it's Unavailable / Degraded and, on the VE's own
// pages, its tabs. The card stays live through the VEs store.
const { id, ve, role, can, cp, reload } = useCurrentVe()
const ves = useVesStore()
const confirmDialog = useConfirm()
const { optimistic } = useOptimistic()
useHead(() => ({ title: `${ve.value?.name ?? 'VE'} · axo` }))

const tabs = computed(() => [
  { path: '', title: 'Overview', icon: 'mdi-view-grid-outline' },
  { path: 'monitoring', title: 'Monitoring', icon: 'mdi-chart-line' },
  { path: 'activity', title: 'Activity', icon: 'mdi-timeline-text-outline' },
  { path: 'settings', title: 'Settings', icon: 'mdi-tune-variant' },
  { path: 'policies', title: 'Policies', icon: 'mdi-auto-fix' },
  ...(can('admin') ? [{ path: 'access', title: 'Access', icon: 'mdi-account-key-outline' },
    { path: 'costs', title: 'Costs', icon: 'mdi-receipt-text-outline' }] : []),
])
const route = useRoute()
const tab = computed(() => route.path.split('/')[3] ?? '')
const onTabs = computed(() => tabs.value.some(t => t.path === tab.value)
  || ['access', 'costs'].includes(tab.value))

const error = ref<unknown>(null)
async function load() {
  error.value = null
  try { await reload() } catch (err) { error.value = err }
}
watch(id, load, { immediate: true })

const planName = (p: string) => (p ? p[0]!.toUpperCase() + p.slice(1) : '—')
const canStart = computed(() => ['Stopped', 'Failed'].includes(ve.value?.lifecycle ?? ''))
const canStop = computed(() => ['Running', 'Scaling', 'Sleeping'].includes(ve.value?.lifecycle ?? ''))

// ── actions ────────────────────────────────────────────────────────────────
const busy = ref<string | null>(null)
async function act(name: string, run: () => Promise<unknown>, guess: Partial<NonNullable<typeof ve.value>>,
  success: string, failure: string) {
  const before = { ...ve.value! }
  busy.value = name
  await optimistic({
    apply: () => ves.put({ ...before, ...guess } as any),
    rollback: () => ves.put(before),
    run, success, failure,
    settle: () => { reload().catch(() => {}) },
  })
  busy.value = null
}

async function start() {
  await act('start', () => cp('/start', { method: 'POST' }), { lifecycle: 'Starting', lifecycle_reason: null },
    `Starting ${ve.value!.name}`, `${ve.value!.name} couldn't start`)
}

async function stop() {
  const name = ve.value!.name
  if (!await confirmDialog.confirm({
    title: `Stop ${name}?`, danger: true, confirmText: 'Stop',
    text: 'Its endpoints shut down; functions, objects and data stay. Start it again whenever you want.',
    consequences: ['Jobs that are running are finished first; new jobs are refused while it is stopped.',
      ...(ve.value!.rental.period_fee_cents ? ['The plan fee is still charged while it is stopped.'] : [])],
  })) return
  await act('stop', () => cp('/stop', { method: 'POST' }), { lifecycle: 'Stopping' }, `Stopping ${name}`, `${name} couldn't stop`)
}

async function cancel() {
  const v = ve.value!
  if (!await confirmDialog.confirm({
    title: `Cancel the rental of ${v.name}?`, danger: true, confirmText: 'Cancel the rental', cancelText: 'Keep it',
    text: `Nothing changes until ${formatDateTime(v.rental.period_end)}: then it stops and isn't renewed.`,
    consequences: ['After that it waits for the grace period, then it is deleted with its data.',
      'You can change your mind until then ("Keep renting").'],
  })) return
  await act('cancel', () => cp('/cancel', { method: 'POST' }),
    { rental: { ...v.rental, cancel_at_period_end: true } }, 'Rental cancelled', "The rental couldn't be cancelled")
}

async function resume() {
  const v = ve.value!
  await act('resume', () => cp('/resume', { method: 'POST' }),
    { rental: { ...v.rental, cancel_at_period_end: false } }, `${v.name} keeps renewing`, "The rental couldn't be resumed")
}

async function remove() {
  const v = ve.value!
  if (!await confirmDialog.confirm({
    title: `Delete ${v.name}?`, danger: true, confirmText: 'Delete', typeToConfirm: v.name,
    text: 'Everything in it is removed: endpoints, functions, objects, choreographies and their history.',
    consequences: ['Your buckets are not part of the VE and stay.', 'Bills already made stay in Billing.',
      'This cannot be undone.'],
  })) return
  try {
    await cp(`?confirm=${encodeURIComponent(v.name)}`, { method: 'DELETE' })
    ves.put({ ...v, lifecycle: 'Deleting' })
    useNotifier().success(`Deleting ${v.name}`)
    await navigateTo('/ves')
  } catch (err) { useNotifier().error(err, `${v.name} couldn't be deleted`) }
}

const planDialog = ref(false)

// ── repair (Admins; once every 5 minutes per VE) ───────────────────────────
const repairDialog = ref(false)
const repairAvailableAt = ref<number | null>(null)
const now = ref(Date.now() / 1000)
let ticker: ReturnType<typeof setInterval> | null = null
onMounted(() => { ticker = setInterval(() => { now.value = Date.now() / 1000 }, 15000) })
onBeforeUnmount(() => { if (ticker) clearInterval(ticker) })
const canRepair = computed(() => !['Stopped', 'Stopping', 'Sleeping', 'Deleting', 'Deleted'].includes(ve.value?.lifecycle ?? ''))
const repairWait = computed(() => Math.max(0, (repairAvailableAt.value ?? 0) - now.value))
const repairSubtitle = computed(() => {
  if (!canRepair.value) return 'Only a running VE'
  if (repairWait.value > 0) return `Available again in ${Math.ceil(repairWait.value / 60)} min`
  return 'Check every piece and fix what it can'
})
async function loadRepair() {
  now.value = Date.now() / 1000
  try { repairAvailableAt.value = (await cp<{ available_at: number | null }>('/repair')).available_at } catch { /* the menu still works */ }
}
function afterRepair() {
  now.value = Date.now() / 1000
  repairAvailableAt.value = now.value + 300
  reload().catch(() => {})
}
watch(() => ve.value?.health, (h) => { if (h === 'Unavailable' || h === 'Degraded') loadRepair() }, { immediate: true })
</script>

<style scoped>
.axo-ve-tabs { border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
</style>
