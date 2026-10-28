<template>
  <div>
    <AxoErrorAlert v-if="error" :error="error" title="The costs couldn't load" :retry="load" class="mb-4" />
    <AxoSkeleton v-if="!data && !error" :lines="10" />

    <template v-if="data">
      <v-row density="compact">
        <v-col cols="12" lg="7">
          <AxoPanel title="This period so far" icon="mdi-receipt-text-clock-outline"
                    help="The open bill, live: the plan fee plus what jobs used. It closes an hour after the period ends.">
            <VeBill v-if="data.current" :bill="data.current" />
            <div v-else class="text-body-2 axo-muted">No open bill (the rental has ended).</div>
          </AxoPanel>
        </v-col>
        <v-col cols="12" lg="5">
          <AxoPanel title="Budget" icon="mdi-piggy-bank-outline" stacked
                    help="A warning at 80 % and 100 % of the bill, per period. Jobs never stop because of it.">
            <template v-if="data.budget_cents != null">
              <div class="d-flex align-baseline ga-2 mb-2">
                <span class="text-h5 font-weight-bold">{{ formatPercent(data.budget_used_pct ?? 0, 0) }}</span>
                <span class="text-body-2 axo-muted">of {{ formatMoney(data.budget_cents) }} used</span>
              </div>
              <v-progress-linear :model-value="Math.min(100, data.budget_used_pct ?? 0)" height="8" rounded
                                 :color="(data.budget_used_pct ?? 0) >= 100 ? 'error' : (data.budget_used_pct ?? 0) >= 80 ? 'warning' : 'primary'" />
            </template>
            <div v-else class="text-body-2 axo-muted">No budget set.</div>
            <div v-if="can('owner')" class="d-flex align-center ga-2 mt-4">
              <v-text-field v-model.number="budgetDollars" type="number" min="0" step="1" prefix="$" label="Per period" density="compact"
                            hide-details clearable style="max-width: 180px" />
              <AxoBtn :loading="savingBudget" @click="saveBudget">{{ budgetDollars == null || budgetDollars === '' ? 'Remove' : 'Set' }}</AxoBtn>
            </div>
          </AxoPanel>

          <AxoPanel title="Past bills" icon="mdi-receipt-text-check-outline" class="mt-4" flush stacked>
            <v-list density="compact" class="bg-transparent">
              <v-list-item v-for="b in data.bills" :key="b.period_index" :title="`${formatDate(b.period_start)} – ${formatDate(b.period_end)}`"
                           @click="openBill(b)">
                <template #append><span class="font-weight-medium">{{ formatMoney(b.total_cents) }}</span></template>
              </v-list-item>
              <v-list-item v-if="!data.bills.length" class="axo-muted" title="None yet: the first closes after this period." />
            </v-list>
          </AxoPanel>
          <VeStored class="mt-4" />
        </v-col>
      </v-row>

      <AxoPanel title="Cost per function this period" icon="mdi-function-variant" class="mt-4" flush>
        <v-table class="bg-transparent">
          <thead><tr><th>Function</th><th class="text-end">Jobs</th><th class="text-end">Billable</th><th class="text-end">Run time</th><th class="text-end">Cost</th></tr></thead>
          <tbody>
            <tr v-for="row in data.functions" :key="row.function_id">
              <td class="font-weight-medium">{{ fname(row.function_id) }}</td>
              <td class="text-end">{{ formatNumber(row.attempts) }}</td>
              <td class="text-end">{{ formatNumber(row.billable_attempts) }}</td>
              <td class="text-end">{{ formatMs(row.execution_ms) }}</td>
              <td class="text-end">{{ formatUcents(row.cost_ucents) }}</td>
            </tr>
            <tr v-if="!data.functions.length"><td colspan="5" class="text-center axo-muted py-6">No jobs this period yet.</td></tr>
          </tbody>
        </v-table>
      </AxoPanel>

      <AxoPanel v-if="choreographies.length" title="Choreographies this period" icon="mdi-sitemap-outline" class="mt-4" flush
                help="What their step jobs cost (already part of the bill above; a choreography itself is never charged).">
        <v-table class="bg-transparent">
          <thead><tr><th>Choreography</th><th class="text-end">Step jobs</th><th class="text-end">Cost</th></tr></thead>
          <tbody>
            <tr v-for="row in choreographies" :key="row.choreography_id">
              <td class="font-weight-medium">{{ chname(row.choreography_id) }}</td>
              <td class="text-end">{{ formatNumber(row.attempts) }}</td>
              <td class="text-end">{{ formatUcents(row.cost_ucents) }}</td>
            </tr>
          </tbody>
        </v-table>
      </AxoPanel>
    </template>

    <v-dialog v-model="billOpen" max-width="640">
      <v-card rounded="lg">
        <v-card-title class="pt-5 px-6">Bill</v-card-title>
        <v-card-text class="px-6">
          <AxoSkeleton v-if="!bill" :lines="6" />
          <VeBill v-else :bill="bill" />
        </v-card-text>
        <v-card-actions class="px-6 pb-5"><v-spacer /><AxoBtn look="plain" variant="text" @click="billOpen = false">Close</AxoBtn></v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
// The VE's money (Owner / Admin): the open bill live, the budget (Owner
// sets it), past bills, cost per function and per choreography.
const { id, ve, can, api, cp } = useCurrentVe()
const notify = useNotifier()

const data = ref<any>(null)
const choreographies = ref<any[]>([])
const names = ref<Record<string, string>>({})
const chNames = ref<Record<string, string>>({})
const error = ref<unknown>(null)
const budgetDollars = ref<number | string | null>(null)

async function load() {
  error.value = null
  try {
    const [costs, ch] = await Promise.all([cp('/costs'), cp('/costs/choreographies').catch(() => null)])
    data.value = costs
    choreographies.value = ch?.choreographies ?? []
    budgetDollars.value = costs.budget_cents != null ? costs.budget_cents / 100 : null
  } catch (err) { error.value = err }
  if (!['Stopped', 'Sleeping', 'Deleting'].includes(ve.value?.lifecycle ?? '')) {
    api<Array<{ function_id: string; name: string }>>('/functions')
      .then(fns => { names.value = Object.fromEntries(fns.map(f => [f.function_id, f.name])) }).catch(() => {})
    api<Array<{ choreography_id: string; name: string }>>('/choreographies')
      .then(list => { chNames.value = Object.fromEntries(list.map(c => [c.choreography_id, c.name])) }).catch(() => {})
  }
}
const fname = (fid?: string) => (fid ? names.value[fid] ?? `${fid.slice(0, 12)}…` : '—')
const chname = (cid?: string) => (cid ? chNames.value[cid] ?? cid : '—')
watch(id, load)
usePolling(load, 60_000)

const savingBudget = ref(false)
async function saveBudget() {
  savingBudget.value = true
  const value = budgetDollars.value === '' || budgetDollars.value == null ? null : Math.round(Number(budgetDollars.value) * 100)
  try {
    await cp('/budget', { method: 'PUT', body: { budget_cents: value } })
    notify.success(value == null ? 'Budget removed' : `Budget set to ${formatMoney(value)} per period`)
    await load()
  } catch (err) { notify.error(err, "The budget couldn't be set") } finally { savingBudget.value = false }
}

const billOpen = ref(false)
const bill = ref<any>(null)
async function openBill(b: any) {
  bill.value = null
  billOpen.value = true
  try { bill.value = await cp(`/bills/${b.period_index}`) } catch (err) { billOpen.value = false; notify.error(err, "The bill couldn't load") }
}
</script>
