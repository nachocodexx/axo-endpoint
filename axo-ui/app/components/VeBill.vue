<template>
  <div>
    <div v-if="!bare" class="d-flex align-center flex-wrap ga-2 mb-3">
      <span class="text-body-2 axo-muted">{{ formatDate(bill.period_start) }} – {{ formatDate(bill.period_end) }}</span>
      <AxoStatusChip kind="bill" :value="bill.status" size="x-small" />
      <v-spacer />
      <span class="text-h5 font-weight-bold">{{ formatMoney(bill.total_cents) }}</span>
    </div>
    <v-table density="compact" class="bg-transparent axo-bill">
      <tbody>
        <tr v-for="(f, i) in bill.fees" :key="`fee-${i}`">
          <td>{{ FEE_NAMES[f.kind] ?? f.kind }} · {{ f.plan_id }} plan <span class="text-caption axo-muted">{{ formatDate(f.at) }}</span></td>
          <td class="text-end">{{ formatMoney(f.amount_cents) }}</td>
        </tr>
        <tr>
          <td>Compute <span class="text-caption axo-muted">{{ formatNumber(bill.usage.core_seconds, false) }} core-seconds</span></td>
          <td class="text-end">{{ formatUcents(bill.usage.compute_ucents) }}</td>
        </tr>
        <tr>
          <td>Memory <span class="text-caption axo-muted">{{ formatNumber(bill.usage.gb_seconds, false) }} GB-seconds</span></td>
          <td class="text-end">{{ formatUcents(bill.usage.memory_ucents) }}</td>
        </tr>
        <tr>
          <td>Requests <span class="text-caption axo-muted">{{ formatNumber(bill.usage.requests, false) }}
            · {{ formatNumber(bill.usage.billable_attempts, false) }} of {{ formatNumber(bill.usage.attempts, false) }} jobs billable</span></td>
          <td class="text-end">{{ formatUcents(bill.usage.request_ucents) }}</td>
        </tr>
        <tr v-if="bill.object_state?.hours">
          <td>Object state <span class="text-caption axo-muted">{{ bill.object_state.gb_months?.toFixed?.(6) }} GB-months
            · up to {{ formatBytes(bill.object_state.max_bytes) }}</span></td>
          <td class="text-end">{{ formatUcents(bill.object_state.ucents) }}</td>
        </tr>
        <tr v-for="(late, i) in bill.late_usage" :key="`late-${i}`">
          <td>Late usage {{ late.from_period_start ? `from ${formatDate(late.from_period_start)}` : 'from outside a period' }} <span class="text-caption axo-muted">({{ late.attempts }} jobs)</span></td>
          <td class="text-end">{{ formatUcents(late.total_ucents) }}</td>
        </tr>
        <tr class="font-weight-bold">
          <td>Total <span class="text-caption axo-muted font-weight-regular">usage rounded to the cent once per bill</span></td>
          <td class="text-end">{{ formatMoney(bill.total_cents) }}</td>
        </tr>
      </tbody>
    </v-table>
  </div>
</template>

<script setup lang="ts">
// One VE bill: plan fees, usage (compute, memory, requests, object state,
// late usage from earlier periods), total.
// `bare`: without its period / status / total line (a page that shows them).
defineProps<{ bill: any; bare?: boolean }>()
const FEE_NAMES: Record<string, string> = { plan_fee: 'Plan fee', upgrade: 'Upgrade difference', renewal: 'Renewal' }
</script>

<style scoped>
.axo-bill td { padding-top: 8px !important; padding-bottom: 8px !important; }
</style>
