<template>
  <div>
    <AxoPageHeader title="Rent a VE" icon="mdi-cube-outline" :crumbs="[{ title: 'VEs', to: '/ves' }, { title: 'Rent' }]"
                   subtitle="Pick a plan, size it, and it starts right away." />

    <AxoErrorAlert v-if="loadError" :error="loadError" title="The plans couldn't load" :retry="loadPlans" class="mb-4" />

    <v-stepper v-model="step" :items="['Plan', 'Setup', 'Review']" hide-actions flat class="axo-rent bg-transparent">
      <!-- 1. Plan -->
      <template #item.1>
        <AxoSkeleton v-if="!plans.length && !loadError" :lines="6" />
        <div class="axo-plans">
          <v-card v-for="p in plans" :key="p.plan_id"
                  :class="['axo-plan pa-4', { 'axo-plan--on': p.plan_id === planId, 'axo-plan--off': !!blocked(p) }]"
                  :disabled="!!blocked(p)" @click="pick(p)">
            <div class="d-flex align-center">
              <span class="text-h6 font-weight-bold">{{ p.name }}</span>
              <v-spacer />
              <v-icon v-if="p.plan_id === planId" color="primary">mdi-check-circle</v-icon>
            </div>
            <div class="mt-1 mb-3">
              <span class="axo-plan__price">{{ formatMoney(p.monthly_fee_cents) }}</span>
              <span class="text-body-2 axo-muted"> / month{{ p.monthly_fee_cents ? ' + usage' : '' }}</span>
            </div>
            <ul class="axo-plan__facts text-body-2">
              <li v-for="fact in facts(p)" :key="fact">{{ fact }}</li>
            </ul>
            <div v-if="blocked(p)" class="text-caption text-warning mt-3">{{ blocked(p) }}</div>
          </v-card>
        </div>
        <div class="d-flex mt-6">
          <v-spacer />
          <AxoBtn :disabled="!plan" append-icon="mdi-arrow-right" @click="step = 2">Next</AxoBtn>
        </div>
      </template>

      <!-- 2. Setup -->
      <template #item.2>
        <v-form v-if="plan" ref="form" v-model="valid" @submit.prevent="step = 3">
          <v-row>
            <v-col cols="12" md="6">
              <v-text-field v-model="name" label="Name" :rules="nameRules" counter="32" autofocus
                            hint="Lowercase letters, digits and '-'. It's part of the VE's address." persistent-hint />
            </v-col>
            <v-col cols="12" md="6">
              <v-select v-model="teamId" :items="owners" label="Owner" :disabled="plan.personal_only"
                        :hint="plan.personal_only ? `The ${plan.name} plan is personal only.` : 'A team VE is billed to the team.'"
                        persistent-hint />
            </v-col>
          </v-row>

          <div class="text-subtitle-1 font-weight-medium mt-6 mb-2 d-flex align-center ga-1">
            Endpoint size
            <AxoHelp title="Endpoint size" text="Each endpoint is a machine that runs your jobs, with this much CPU and memory for itself and its runners. Bigger endpoints run more at once." />
          </div>
          <div v-if="plan.fixed_endpoint_size" class="text-body-2 axo-muted mb-2">
            The {{ plan.name }} plan uses {{ plan.fixed_endpoint_size }} endpoints
            ({{ describe(sizes[plan.fixed_endpoint_size]) }}).
          </div>
          <v-btn-toggle v-else v-model="sizeName" mandatory divided variant="outlined" color="primary" class="flex-wrap mb-2 axo-sizes">
            <v-btn v-for="(s, n) in sizes" :key="n" :value="n" class="text-none">
              <div class="text-left"><div class="font-weight-medium text-capitalize">{{ n }}</div>
                <div class="text-caption">{{ describe(s) }}</div></div>
            </v-btn>
            <v-btn value="custom" class="text-none"><div class="text-left"><div class="font-weight-medium">Custom</div>
              <div class="text-caption">your numbers</div></div></v-btn>
          </v-btn-toggle>
          <v-row v-if="sizeName === 'custom' && !plan.fixed_endpoint_size" density="compact" class="mt-2">
            <v-col cols="6" md="3"><v-text-field v-model.number="customCpu" type="number" step="0.5" min="0.25" label="CPU cores" /></v-col>
            <v-col cols="6" md="3"><v-text-field v-model.number="customMem" type="number" step="256" min="256" label="Memory (MB)" /></v-col>
          </v-row>

          <v-row class="mt-4">
            <v-col cols="12" md="6">
              <div class="text-body-2 mb-1">Endpoints: <b>{{ target }}</b>
                <span class="axo-muted">(the {{ plan.name }} plan allows up to {{ plan.max_endpoints }})</span></div>
              <v-slider v-model="target" :min="1" :max="plan.max_endpoints" :step="1" show-ticks="always" thumb-label
                        :disabled="plan.max_endpoints === 1" hide-details />
            </v-col>
            <v-col cols="12" md="6">
              <div class="text-body-2 mb-1 d-flex align-center ga-1">Always keep at least: <b>{{ minEndpoints }}</b>
                <AxoHelp title="Minimum endpoints" text="Scaling policies and idle sleeping never go below this. 0 lets every endpoint sleep when nothing runs." /></div>
              <v-slider v-model="minEndpoints" :min="0" :max="target" :step="1" show-ticks="always" thumb-label hide-details />
            </v-col>
          </v-row>

          <template v-if="!plan.fixed_endpoint_size">
            <div class="text-subtitle-1 font-weight-medium mt-6 mb-2 d-flex align-center ga-1">
              Quota
              <AxoHelp title="Quota" text="The most CPU and memory all this VE's endpoints may use together. Room above what the endpoints need lets you add endpoints (or policies add them) later. It's reserved for you, and not billed by itself: you pay for what jobs use." />
            </div>
            <v-row density="compact">
              <v-col cols="6" md="3">
                <v-text-field v-model.number="quotaCpu" type="number" step="0.5" :min="needed.cpu_cores" label="CPU cores"
                              :rules="[v => v >= needed.cpu_cores || `At least ${needed.cpu_cores}`]" />
              </v-col>
              <v-col cols="6" md="3">
                <v-text-field v-model.number="quotaMem" type="number" step="256" :min="needed.memory_mb" label="Memory (MB)"
                              :rules="[v => v >= needed.memory_mb || `At least ${needed.memory_mb}`]" />
              </v-col>
              <v-col cols="12" md="6" class="text-body-2 axo-muted d-flex align-center">
                {{ target }} × {{ describe(size) }} needs {{ describe(needed) }}.
              </v-col>
            </v-row>
          </template>

          <div class="d-flex mt-6">
            <AxoBtn look="plain" variant="text" prepend-icon="mdi-arrow-left" @click="step = 1">Back</AxoBtn>
            <v-spacer />
            <AxoBtn type="submit" :disabled="!valid" append-icon="mdi-arrow-right">Review</AxoBtn>
          </div>
        </v-form>
      </template>

      <!-- 3. Review -->
      <template #item.3>
        <template v-if="plan">
          <v-row>
            <v-col cols="12" md="7">
              <AxoPanel title="Your VE" icon="mdi-cube-outline">
                <AxoKeyValue :columns="2" :items="[
                  { label: 'Name', value: name, mono: true },
                  { label: 'Owner', value: ownerTitle },
                  { label: 'Plan', value: plan.name },
                  { label: 'Endpoints', value: `${target} (at least ${minEndpoints})` },
                  { label: 'Endpoint size', value: `${effectiveSizeName} · ${describe(size)}` },
                  { label: 'Quota', value: describe(quota) },
                  { label: 'History kept', value: `${plan.max_retention_days} days` },
                  { label: 'Scaling policies', value: plan.policies_allowed ? 'Yes' : 'No' },
                ]" />
              </AxoPanel>
            </v-col>
            <v-col cols="12" md="5">
              <AxoPanel title="What it costs" icon="mdi-receipt-text-outline">
                <div class="text-h5 font-weight-bold">{{ formatMoney(plan.monthly_fee_cents, { free: true }) }}
                  <span v-if="plan.monthly_fee_cents" class="text-body-2 axo-muted">now, then every month</span></div>
                <div v-if="plan.monthly_fee_cents" class="text-body-2 axo-muted mt-1">
                  Renews on the same day each month until you cancel. Plus what your jobs use:</div>
                <div v-else class="text-body-2 axo-muted mt-1">
                  Up to {{ formatNumber(plan.request_limit) }} requests a month. Sleeps after 30 idle minutes and wakes on the next job.</div>
                <ul v-if="plan.monthly_fee_cents && prices" class="text-body-2 mt-2 axo-plan__facts">
                  <li>{{ formatUcents(prices.per_core_second_ucents) }} per CPU core-second</li>
                  <li>{{ formatUcents(prices.per_gb_second_ucents) }} per GB-second of memory</li>
                  <li>{{ formatUcents(prices.per_request_ucents) }} per request</li>
                  <li>{{ formatUcents(prices.object_state_per_gb_month_ucents) }} per GB-month of object state</li>
                </ul>
                <div class="text-caption axo-muted mt-2">Only jobs that ran your code are billed; platform errors and jobs that waited too long are not.</div>
              </AxoPanel>
            </v-col>
          </v-row>
          <div class="d-flex mt-6">
            <AxoBtn look="plain" variant="text" prepend-icon="mdi-arrow-left" @click="step = 2">Back</AxoBtn>
            <v-spacer />
            <AxoBtn :loading="renting" prepend-icon="mdi-check" @click="rent">
              {{ plan.monthly_fee_cents ? `Rent for ${formatMoney(plan.monthly_fee_cents)}` : 'Start it' }}
            </AxoBtn>
          </div>
        </template>
      </template>
    </v-stepper>
  </div>
</template>

<script setup lang="ts">
// Renting a VE: plan cards, then name / owner / endpoint size / count /
// quota (checked here the way the Control Plane checks them), then a
// review with the price. POST /ves: "created" opens the new VE, "pending"
// (a Team Admin over the team's spending limit) waits for the Team Owner.
useHead({ title: 'Rent a VE · axo' })
const { apiFetch } = useApi()
const me = useMeStore()
const ves = useVesStore()
const notify = useNotifier()
const route = useRoute()

interface Size { cpu_cores: number; memory_mb: number }
const step = ref(1)
const plans = ref<any[]>([])
const prices = ref<Record<string, number> | null>(null)
const sizes = ref<Record<string, Size>>({})
const loadError = ref<unknown>(null)

async function loadPlans() {
  loadError.value = null
  try {
    const answer = await apiFetch<{ plans: any[]; prices: Record<string, number>; endpoint_sizes: Record<string, Size> }>('/plans')
    plans.value = answer.plans.filter(p => p.active).sort((a, b) => a.order - b.order)
    prices.value = answer.prices
    sizes.value = answer.endpoint_sizes
    const wanted = plans.value.find(p => p.plan_id === route.query.plan)
    if (wanted && !blocked(wanted)) pick(wanted)
  } catch (err) { loadError.value = err }
}
onMounted(() => { loadPlans(); if (!ves.loaded) ves.load() })

// ── 1. plan ────────────────────────────────────────────────────────────────
const planId = ref<string | null>(null)
const plan = computed(() => plans.value.find(p => p.plan_id === planId.value))

function facts(p: any): string[] {
  const out = [`Up to ${p.max_endpoints} endpoint${p.max_endpoints === 1 ? '' : 's'}`,
    `${p.max_retention_days} days of history`]
  if (p.fixed_endpoint_size) out.push(`${p.fixed_endpoint_size[0].toUpperCase()}${p.fixed_endpoint_size.slice(1)} endpoints`)
  out.push(p.policies_allowed ? 'Scaling policies' : 'No scaling policies')
  if (p.request_limit) out.push(`${formatNumber(p.request_limit)} requests a month`)
  if (p.sleeps) out.push('Sleeps when idle')
  out.push(`Objects up to ${formatBytes(p.object_max_state_bytes)} each`)
  if (p.personal_only) out.push('Personal only (no teams)')
  return out
}

function blocked(p: any): string | null {
  if (!p.one_per_user || !me.me) return null
  const mine = ves.list.some(v => v.plan_id === p.plan_id && v.owner.kind === 'user' && v.owner.id === me.me!.user_id)
  return mine ? `You already have a ${p.name} VE (one per person).` : null
}

function pick(p: any) {
  if (blocked(p)) return
  planId.value = p.plan_id
  target.value = Math.min(target.value, p.max_endpoints)
  if (p.personal_only) teamId.value = null
}

// ── 2. setup ───────────────────────────────────────────────────────────────
const form = ref()
const valid = ref(false)
const name = ref('')
const nameRules = [
  (v: string) => /^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$/.test(v || '')
    || "1-32 lowercase letters, digits or '-', not starting or ending with '-'",
]
const teamId = ref<string | null>((me.me?.teams ?? [])
  .find(t => t.name === route.query.team && (t.role === 'owner' || t.role === 'admin'))?.team_id ?? null)
const owners = computed(() => [
  { title: `Me (${me.displayName || me.me?.username || ''})`, value: null },
  ...(me.me?.teams ?? []).filter(t => t.role === 'owner' || t.role === 'admin')
    .map(t => ({ title: `Team ${t.name}`, value: t.team_id })),
])
const ownerTitle = computed(() => owners.value.find(o => o.value === teamId.value)?.title ?? '')

const sizeName = ref('small')
const customCpu = ref(1)
const customMem = ref(2048)
const effectiveSizeName = computed(() => plan.value?.fixed_endpoint_size ?? sizeName.value)
const size = computed<Size>(() => (effectiveSizeName.value === 'custom'
  ? { cpu_cores: Number(customCpu.value) || 0, memory_mb: Number(customMem.value) || 0 }
  : sizes.value[effectiveSizeName.value] ?? { cpu_cores: 1, memory_mb: 1024 }))
const target = ref(1)
const minEndpoints = ref(1)
watch(target, (t) => { if (minEndpoints.value > t) minEndpoints.value = t })
const needed = computed<Size>(() => ({ cpu_cores: size.value.cpu_cores * target.value, memory_mb: size.value.memory_mb * target.value }))

// The quota follows what the endpoints need until the person changes it.
const quotaCpu = ref(1)
const quotaMem = ref(1024)
const quotaTouched = ref(false)
watch(needed, (n) => {
  if (!quotaTouched.value || quotaCpu.value < n.cpu_cores) quotaCpu.value = n.cpu_cores
  if (!quotaTouched.value || quotaMem.value < n.memory_mb) quotaMem.value = n.memory_mb
}, { immediate: true })
watch([quotaCpu, quotaMem], ([c, m]) => { if (c !== needed.value.cpu_cores || m !== needed.value.memory_mb) quotaTouched.value = true })
const quota = computed<Size>(() => (plan.value?.fixed_endpoint_size
  ? { cpu_cores: size.value.cpu_cores * plan.value.max_endpoints, memory_mb: size.value.memory_mb * plan.value.max_endpoints }
  : { cpu_cores: Number(quotaCpu.value), memory_mb: Number(quotaMem.value) }))

const describe = (s?: Size) => (s ? `${s.cpu_cores} CPU · ${formatMemoryMb(s.memory_mb)}` : '—')

// ── 3. rent ────────────────────────────────────────────────────────────────
const renting = ref(false)
async function rent() {
  if (!plan.value) return
  renting.value = true
  try {
    const custom = effectiveSizeName.value === 'custom'
    const answer = await apiFetch<{ status: string; ve?: any }>('/ves', {
      method: 'POST',
      body: {
        name: name.value, plan_id: plan.value.plan_id, team_id: teamId.value ?? undefined,
        endpoint_size: effectiveSizeName.value,
        endpoint_cpu_cores: custom ? size.value.cpu_cores : undefined,
        endpoint_memory_mb: custom ? size.value.memory_mb : undefined,
        target_endpoints: target.value, min_endpoints: minEndpoints.value,
        quota_cpu_cores: quota.value.cpu_cores, quota_memory_mb: quota.value.memory_mb,
      },
    })
    if (answer.status === 'created' && answer.ve) {
      ves.put({ ...answer.ve, my_role: 'owner' })
      notify.success(`${answer.ve.name} is starting`)
      await navigateTo(`/ves/${answer.ve.ve_id}`)
    } else {
      notify.info('Sent to the Team Owner: the team is over its spending limit, so they approve this one.')
      await navigateTo('/ves')
    }
  } catch (err) {
    notify.error(err, "The VE couldn't be rented")
  } finally {
    renting.value = false
  }
}
</script>

<style scoped>
.axo-plans { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.axo-plan__price { font-size: 1.6rem; font-weight: 700; }
.axo-plan { cursor: pointer; border: 2px solid transparent; transition: border-color .15s, transform .15s; }
.axo-plan:hover { transform: translateY(-2px); }
.axo-plan--on { border-color: rgb(var(--v-theme-primary)); }
.axo-plan--off { opacity: .55; }
.axo-plan__facts { padding-left: 1.1rem; line-height: 1.8; }
.axo-rent :deep(.v-stepper-header) { box-shadow: none; }
/* The steps slide inside a clipped frame. Focusing Name while it slides in
   used to scroll that frame sideways, cutting the left edge ("ndpoint
   size"): clip without being scrollable, and leave room for the labels
   that float above the first row. */
.axo-rent :deep(.v-stepper-window) { overflow: clip; overflow-clip-margin: 12px; }
.axo-rent :deep(.v-stepper-window-item) { padding-top: 8px; }
/* The size choices wrap on a phone: the toggle's fixed height hid the second row. */
.axo-sizes { height: auto; }
.axo-sizes :deep(.v-btn) { height: auto; min-height: 56px; }
</style>
