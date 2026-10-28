<template>
  <div>
    <v-table density="compact" class="bg-transparent axo-bill">
      <tbody>
        <tr v-for="b in bill.storage?.buckets ?? []" :key="b.bucket_id">
          <td>Storage · <span class="font-weight-medium">{{ b.name ?? b.bucket_id }}</span>
            <span class="text-caption axo-muted">{{ gb(b.gb_months) }} GB-months · up to {{ formatBytes(b.max_bytes) }}</span></td>
          <td class="text-end">{{ formatUcents(b.ucents) }}</td>
        </tr>
        <tr v-if="!bill.storage?.buckets?.length">
          <td class="axo-muted">Storage <span class="text-caption">no bucket kept data this month</span></td>
          <td class="text-end">{{ formatUcents(0) }}</td>
        </tr>
        <tr>
          <td>Requests <span class="text-caption axo-muted">{{ formatNumber(bill.requests?.requests ?? 0, false) }}</span></td>
          <td class="text-end">{{ formatUcents(bill.requests?.request_ucents ?? 0) }}</td>
        </tr>
        <tr>
          <td>Transfer <span class="text-caption axo-muted">{{ formatBytes(bill.requests?.bytes_read ?? 0) }} read
            · {{ formatBytes(bill.requests?.bytes_written ?? 0) }} written (free)</span></td>
          <td class="text-end">{{ formatUcents(bill.requests?.transfer_ucents ?? 0) }}</td>
        </tr>
        <tr v-for="(late, i) in bill.late ?? []" :key="`late-${i}`">
          <td>Late usage from {{ late.from_month }} <span class="text-caption axo-muted">(arrived after that bill closed)</span></td>
          <td class="text-end">{{ formatUcents(late.storage_ucents + late.requests_ucents) }}</td>
        </tr>
        <tr class="font-weight-bold">
          <td>Total <span class="text-caption axo-muted font-weight-regular">rounded to the cent once per bill</span></td>
          <td class="text-end">{{ formatMoney(bill.total_cents) }}</td>
        </tr>
      </tbody>
    </v-table>
  </div>
</template>

<script setup lang="ts">
// One storage bill (a calendar month, UTC): each bucket's kept data in
// GB-months, requests, transfer (bytes read), late items, total.
defineProps<{ bill: any }>()
const gb = (v: number) => (v ?? 0).toFixed(v >= 1 ? 2 : 6)
</script>

<style scoped>
.axo-bill td { padding-top: 8px !important; padding-bottom: 8px !important; }
</style>
