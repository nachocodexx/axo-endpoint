<template>
  <div>
    <AxoPageHeader title="Billing" icon="mdi-receipt-text-outline"
                   subtitle="What you pay for: every VE bill and the monthly storage bill, in one place."
                   help="A VE bill covers one rental period: the plan fee plus what jobs used. The storage bill covers a calendar month (UTC) of your buckets. Bills close an hour after their period ends and never change after that. Nothing is charged to a card yet: bills are information only.">
      <template #actions>
        <v-select v-if="payers.length > 1" v-model="whose" :items="payers" label="Whose bills" density="compact" hide-details
                  style="min-width: 200px" prepend-inner-icon="mdi-account-switch-outline" />
      </template>
    </AxoPageHeader>

    <AxoErrorAlert v-if="error" :error="error" title="The bills couldn't load" :retry="load" class="mb-4" />
    <AxoSkeleton v-if="!data && !error" :lines="10" />

    <template v-if="data">
      <AxoPanel title="So far this month / period" icon="mdi-receipt-text-clock-outline" stacked class="mb-4"
                help="Everything still open, live: each VE's current period and this month's storage. Each closes on its own date.">
        <div class="d-flex align-baseline flex-wrap ga-2 mb-4">
          <span class="text-h4 font-weight-bold">{{ formatMoney(data.open_total_cents) }}</span>
          <span class="text-body-2 axo-muted">open now{{ team ? ` for ${team.name}` : '' }}</span>
        </div>
        <v-row density="compact">
          <v-col v-for="ve in data.ves" :key="ve.ve_id" cols="12" sm="6" lg="4">
            <v-card :to="`/ves/${ve.ve_id}/costs`" variant="outlined" class="axo-open pa-4 h-100">
              <div class="d-flex align-center ga-2 mb-1">
                <v-icon size="18" color="primary">mdi-cube-outline</v-icon>
                <span class="font-weight-medium text-truncate">{{ ve.name }}</span>
                <v-spacer />
                <span class="text-h6 font-weight-bold">{{ ve.current ? formatMoney(ve.current.total_cents) : '—' }}</span>
              </div>
              <div v-if="ve.current" class="text-caption axo-muted">
                {{ formatMoney(ve.current.fees_cents) }} plan + {{ formatMoney(ve.current.usage_cents) }} jobs
                · closes {{ formatDate(ve.current.period_end) }}
              </div>
              <div v-else class="text-caption axo-muted">No open period (the rental has ended).</div>
            </v-card>
          </v-col>
          <v-col cols="12" sm="6" lg="4">
            <v-card :to="`/billing/bills/${encodeURIComponent(storageBillId(data.payer, data.storage.current.month))}${team ? `?team=${team.team_id}` : ''}`"
                    variant="outlined" class="axo-open pa-4 h-100">
              <div class="d-flex align-center ga-2 mb-1">
                <v-icon size="18" color="primary">mdi-database-outline</v-icon>
                <span class="font-weight-medium">Storage</span>
                <v-spacer />
                <span class="text-h6 font-weight-bold">{{ formatMoney(data.storage.current.total_cents) }}</span>
              </div>
              <div class="text-caption axo-muted">
                {{ data.storage.current.storage?.buckets?.length ?? 0 }} buckets · {{ formatNumber(data.storage.current.requests?.requests ?? 0) }} requests
                · closes when {{ monthTitle({ period_start: data.storage.current.period_start } as BillRow) }} ends
              </div>
            </v-card>
          </v-col>
        </v-row>
      </AxoPanel>

      <AxoDataTable :headers="headers" :items="filtered" item-value="bill_id" :searchable="false" :items-per-page="25"
                    title="Past bills" :row-to="(r) => billLink(r.bill_id)" empty-icon="mdi-receipt-text-check-outline"
                    empty-title="No past bills yet" empty-text="The first one closes an hour after the current period or month ends.">
        <template #actions>
          <v-select v-model="filter" :items="filters" density="compact" hide-details style="max-width: 220px" label="Show" />
        </template>
        <template #item.period_start="{ item }">
          <span class="text-body-2 text-no-wrap">{{ item.type === 'storage' ? monthTitle(item) : `${formatDate(item.period_start)} – ${formatDate(item.period_end)}` }}</span>
        </template>
        <template #item.title="{ item }">
          <span class="d-inline-flex align-center ga-2">
            <v-icon size="16" :color="item.type === 'storage' ? 'info' : 'primary'">{{ item.type === 'storage' ? 'mdi-database-outline' : 'mdi-cube-outline' }}</v-icon>
            <span class="font-weight-medium">{{ item.type === 'storage' ? 'Storage' : `VE ${item.title}` }}</span>
            <v-chip v-if="item.ve_deleted" size="x-small" variant="tonal">deleted</v-chip>
            <v-chip v-if="item.late" size="x-small" color="warning" variant="tonal" title="Has usage that arrived after an earlier bill closed">late usage</v-chip>
          </span>
        </template>
        <template #item.total_cents="{ item }"><span class="font-weight-medium">{{ formatMoney(item.total_cents) }}</span></template>
      </AxoDataTable>
    </template>
  </div>
</template>

<script setup lang="ts">
import { pastBills, storageBillId, type BillRow, type PayerView } from '../../utils/billing'

// The person's money (GET /me/billing), or a team's where they are Team
// Owner / Admin (GET /teams/{id}/billing): everything open now (a card per
// VE -> its Costs tab, and this month's storage), then every past bill, VE
// and storage mixed, newest first; a row opens the bill's own page.
useHead({ title: 'Billing · axo' })
const { apiFetch } = useApi()
const me = useMeStore()
const route = useRoute()

const teams = computed(() => (me.me?.teams ?? []).filter(t => t.role === 'owner' || t.role === 'admin'))
const payers = computed(() => [{ title: 'Me', value: 'me' }, ...teams.value.map(t => ({ title: t.name, value: t.team_id }))])
const whose = ref<string>(String(route.query.team ?? 'me'))
const team = computed(() => teams.value.find(t => t.team_id === whose.value) ?? null)

const data = ref<PayerView | null>(null)
const error = ref<unknown>(null)
let request = 0
async function load() {
  const mine = ++request
  error.value = null
  data.value = null
  try {
    const answer = await apiFetch<PayerView>(whose.value === 'me' ? '/me/billing' : `/teams/${whose.value}/billing`)
    if (mine === request) data.value = answer
  } catch (err) {
    if (mine === request) error.value = err
  }
}
onMounted(load)
watch(whose, () => {
  history.replaceState(history.state, '', useRouter().resolve({ path: route.path, query: whose.value === 'me' ? {} : { team: whose.value } }).href)
  filter.value = 'all'
  load()
})

const rows = computed(() => (data.value ? pastBills(data.value) : []))
const filter = ref('all')
const filters = computed(() => {
  const ves = new Map<string, string>()
  for (const r of rows.value) if (r.type === 've' && r.ve_id) ves.set(r.ve_id, r.title)
  return [{ title: 'Every bill', value: 'all' }, { title: 'Storage', value: 'storage' },
    ...[...ves].map(([id, name]) => ({ title: `VE ${name}`, value: id }))]
})
const filtered = computed(() => rows.value.filter(r => filter.value === 'all'
  || (filter.value === 'storage' ? r.type === 'storage' : r.ve_id === filter.value)))

const headers = [
  { title: 'Period', key: 'period_start', sortable: false },
  { title: 'For', key: 'title', sortable: false },
  { title: 'Total', key: 'total_cents', sortable: false, align: 'end' as const },
]
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
function monthTitle(r: BillRow) {
  const d = new Date(r.period_start * 1000)
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}
const billLink = (id: string) => `/billing/bills/${encodeURIComponent(id)}${team.value ? `?team=${team.value.team_id}` : ''}`
</script>

<style scoped>
.axo-open { transition: border-color 0.15s; }
.axo-open:hover { border-color: rgb(var(--v-theme-primary)); }
</style>
