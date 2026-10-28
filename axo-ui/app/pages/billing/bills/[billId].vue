<template>
  <div>
    <AxoPageHeader :title="title" icon="mdi-receipt-text-outline" :crumbs="crumbs" class="axo-no-print">
      <template #actions>
        <template v-if="bill">
          <v-btn variant="text" class="text-none" prepend-icon="mdi-file-delimited-outline" @click="downloadCsv">Download CSV</v-btn>
          <v-btn variant="tonal" color="primary" class="text-none" prepend-icon="mdi-printer-outline" @click="print">Print / Save as PDF</v-btn>
        </template>
      </template>
    </AxoPageHeader>

    <AxoErrorAlert v-if="error" :error="error" title="The bill couldn't load" :retry="load" class="mb-4" />
    <AxoSkeleton v-if="!bill && !error" :lines="10" />

    <v-card v-if="bill" class="axo-paper pa-6 pa-md-8" max-width="880">
      <div class="d-flex flex-wrap align-start ga-4 mb-6">
        <div class="min-w-0">
          <div class="text-overline axo-muted">axo · {{ bill.type === 'storage' ? 'Storage bill' : 'VE bill' }}</div>
          <div class="text-h5 font-weight-bold">{{ title }}</div>
          <div class="text-body-2 axo-muted mt-1">{{ periodText }}</div>
        </div>
        <v-spacer />
        <div class="text-end">
          <AxoStatusChip kind="bill" :value="bill.status" size="small" />
          <div class="text-h4 font-weight-bold mt-2">{{ formatMoney(bill.total_cents) }}</div>
        </div>
      </div>

      <AxoKeyValue :items="facts" class="mb-6" />

      <v-alert v-if="bill.status === 'open'" type="info" variant="tonal" density="compact" class="mb-4 axo-no-print">
        This bill is still open: the figures grow until it closes, an hour after {{ bill.type === 'storage' ? 'the month ends (UTC)' : formatDate(bill.period_end) }}.
      </v-alert>

      <StorageBill v-if="bill.type === 'storage'" :bill="bill" />
      <VeBill v-else :bill="bill" bare />

      <div class="text-caption axo-muted mt-6">
        Information only: nothing is charged to a card yet.
        <template v-if="bill.type === 'storage'">Months follow the UTC calendar.</template>
        Bill {{ bill.bill_id ?? billId }}
      </div>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { billCsv } from '../../../utils/billing'
import { saveBlob } from '../../../utils/download'

// One bill (GET /billing/bills/{id}) with its own address: a VE bill (fees,
// jobs, object state, late usage) or a storage bill (buckets, requests,
// transfer); Print / Save as PDF (the browser's print, page chrome hidden)
// and the lines as CSV.
const route = useRoute()
const { apiFetch } = useApi()
const me = useMeStore()
const billId = computed(() => String(route.params.billId))

const bill = ref<any>(null)
const error = ref<unknown>(null)
async function load() {
  error.value = null
  bill.value = null
  try { bill.value = await apiFetch(`/billing/bills/${encodeURIComponent(billId.value)}`) } catch (err) { error.value = err }
}
onMounted(load)
watch(billId, load)

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const monthTitle = (ts: number) => { const d = new Date(ts * 1000); return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}` }
const title = computed(() => {
  if (!bill.value) return 'Bill'
  return bill.value.type === 'storage' ? `Storage · ${monthTitle(bill.value.period_start)}` : `VE ${bill.value.ve_name ?? bill.value.ve_id}`
})
// A storage bill is a calendar month in UTC: its name, not its edges in the
// person's own zone (which would read "08/31 – 09/30").
const periodText = computed(() => {
  if (!bill.value) return ''
  if (bill.value.type === 'storage') return `${monthTitle(bill.value.period_start)} (UTC calendar month)`
  return `${formatDate(bill.value.period_start)} – ${formatDate(bill.value.period_end)}`
})
useHead({ title: computed(() => `${title.value} · Billing · axo`) })

const teamQuery = computed(() => (route.query.team ? `?team=${route.query.team}` : ''))
const crumbs = computed(() => [{ title: 'Billing', to: `/billing${teamQuery.value}` }, { title: title.value, disabled: true }])

function payerName(p: { kind: string; id: string; name?: string } | undefined) {
  if (!p) return '—'
  if (p.kind === 'user') return p.id === me.me?.user_id ? `You (${me.me?.username})` : (p.name ?? 'A person')
  return `Team ${me.me?.teams.find(t => t.team_id === p.id)?.name ?? p.name ?? p.id}`
}
const facts = computed(() => {
  const b = bill.value
  if (!b) return []
  const out: Array<{ label: string; value: string }> = [
    { label: 'Paid by', value: payerName(b.type === 'storage' ? b.payer : b.owner) },
    { label: 'Period', value: periodText.value },
  ]
  if (b.type === 've') {
    out.push({ label: 'VE', value: `${b.ve_name ?? b.ve_id}${b.ve_deleted ? ' (deleted)' : ''}` })
    if (b.fees?.[0]?.plan_id) out.push({ label: 'Plan', value: b.fees[0].plan_id })
  }
  out.push({ label: b.status === 'open' ? 'Closes' : 'Closed', value: b.closed_at ? formatDateTime(b.closed_at) : `about an hour after ${b.type === 'storage' ? 'the month ends' : formatDate(b.period_end)}` })
  return out
})

function print() { window.print() }

function downloadCsv() {
  const name = bill.value.type === 'storage'
    ? `axo-storage-${bill.value.month ?? 'bill'}.csv`
    : `axo-${bill.value.ve_name ?? bill.value.ve_id}-period-${bill.value.period_index}.csv`
  saveBlob(new Blob([billCsv(bill.value)], { type: 'text/csv' }), name)
}
</script>

<style scoped>
@media print {
  .axo-no-print { display: none !important; }
  .axo-paper { box-shadow: none !important; border: none !important; max-width: none !important; padding: 0 !important; }
}
</style>

<style>
@media print {
  .v-navigation-drawer, .v-app-bar, .axo-snackbars, .axo-upload-panel { display: none !important; }
  .v-main { padding: 0 !important; margin: 0 !important; --v-layout-left: 0px !important; --v-layout-top: 0px !important; }
  .axo-content { padding: 0 !important; }
  body, .v-application { background: #fff !important; }
}
</style>
