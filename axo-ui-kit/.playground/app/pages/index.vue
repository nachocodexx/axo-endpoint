<template>
  <div>
    <AxoPageHeader title="axo-ui-kit" icon="mdi-palette-swatch-outline" subtitle="Every shared component, with made-up data."
                   help="This page only exists in the kit's playground." :crumbs="[{ title: 'Kit' }, { title: 'Components' }]">
      <template #actions>
        <AxoRangePicker v-model="range" />
        <AxoBtn prepend-icon="mdi-plus">Rent a VE</AxoBtn>
      </template>
    </AxoPageHeader>

    <v-row>
      <v-col v-for="s in stats" :key="s.label" cols="12" sm="6" lg="3"><AxoStatCard v-bind="s" /></v-col>
    </v-row>

    <v-row>
      <v-col cols="12" lg="7">
        <AxoPanel title="Jobs" icon="mdi-chart-bar" help="Finished jobs per step; red = failed.">
          <AxoTimeChart :series="jobs" />
        </AxoPanel>
      </v-col>
      <v-col cols="12" lg="5">
        <AxoPanel title="Response time" icon="mdi-timer-outline" help="Half of the jobs took less than the median; 95 % less than p95.">
          <AxoTimeChart :series="latency" :format="formatMs" zoom />
        </AxoPanel>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12" lg="7">
        <AxoDataTable title="Virtual environments" :headers="headers" :items="ves" item-value="ve_id"
                      :pending="(v) => v.lifecycle === 'Stopping'" :row-to="() => undefined" @row="(v) => notify.info(`Clicked ${v.name}`)">
          <template #item.lifecycle="{ item }"><AxoStatusChip :value="item.lifecycle" /></template>
          <template #item.health="{ item }"><AxoStatusChip kind="health" :value="item.health" /></template>
          <template #item.ve_id="{ item }"><AxoCopy :text="item.ve_id" max-width="110px" /></template>
          <template #actions><AxoBtn look="plain" variant="text" prepend-icon="mdi-refresh">Refresh</AxoBtn></template>
        </AxoDataTable>
      </v-col>
      <v-col cols="12" lg="5">
        <AxoPanel title="When jobs run" icon="mdi-calendar-clock" help="Darker = more jobs in that hour.">
          <AxoHeatmap :cells="heat" />
        </AxoPanel>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12" md="6">
        <AxoPanel title="Snackbars, confirmations, errors" icon="mdi-message-badge-outline">
          <div class="axo-gap">
            <AxoBtn look="plain" color="success" @click="notify.success('VE ml-prod is running', { to: '/' })">Success</AxoBtn>
            <AxoBtn look="plain" color="info" @click="notify.info('Endpoint ep-3 joined')">Info</AxoBtn>
            <AxoBtn look="plain" color="warning" @click="notify.warning('90 % of the budget used')">Warning</AxoBtn>
            <AxoBtn look="plain" color="error" @click="fail">Error</AxoBtn>
            <AxoBtn look="danger" prepend-icon="mdi-delete" @click="remove">Delete VE</AxoBtn>
            <AxoBtn :loading="busy" @click="optimisticDemo">Optimistic stop</AxoBtn>
          </div>
          <AxoErrorAlert class="mt-4" :error="sampleError" title="Costs couldn't be loaded" :retry="() => notify.info('Retrying')" />
        </AxoPanel>
      </v-col>
      <v-col cols="12" md="6">
        <AxoPanel title="Details and loading" icon="mdi-format-list-text">
          <AxoKeyValue :items="[
            { label: 'Plan', value: 'Standard' }, { label: 'Endpoints', value: '3 of 4', help: 'Running of the target.' },
            { label: 'VE id', value: 've-7f3a', mono: true }, { label: 'Period', value: '20 Oct – 20 Nov' },
            { label: 'Endpoint size', value: '2 cores · 4 GB' }, { label: 'Budget', value: '$40.00' }]" />
          <div class="d-flex align-center ga-8 mt-6">
            <AxoLoader text="Loading" />
            <AxoSkeleton class="flex-grow-1" />
          </div>
          <AxoEmptyState class="mt-4" icon="mdi-cube-outline" title="Rent your first VE" text="A VE runs your functions on its own endpoints.">
            <AxoBtn prepend-icon="mdi-plus">Rent a VE</AxoBtn>
          </AxoEmptyState>
        </AxoPanel>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { AxoApiError } from '../../../app/utils/apiError'
import { CHART_COLORS } from '../../../app/utils/status'
import type { RangeValue } from '../../../app/composables/useTimeRange'

definePageMeta({ auth: false })
const notify = useNotifier()
const { confirm } = useConfirm()
const { optimistic, pending: busy } = useOptimistic()
const range = ref<RangeValue>('24h')

const now = Math.floor(Date.now() / 1000 / 3600) * 3600
const hours = Array.from({ length: 24 }, (_, i) => now - (23 - i) * 3600)
const wave = (i: number, base: number, amp: number) => Math.max(0, Math.round(base + amp * Math.sin(i / 3) + Math.random() * amp / 2))
const done = hours.map((t, i) => [t, wave(i, 60, 30)] as [number, number])
const failed = hours.map((t, i) => [t, wave(i, 4, 3)] as [number, number])
const jobs = [
  { name: 'Completed', points: done, type: 'bar' as const, stack: 'jobs', color: CHART_COLORS.blue },
  { name: 'Failed', points: failed, type: 'bar' as const, stack: 'jobs', color: CHART_COLORS.red },
]
const latency = [
  { name: 'Median', points: hours.map((t, i) => [t, 120 + wave(i, 20, 15)] as [number, number]), color: CHART_COLORS.blue },
  { name: 'p95', points: hours.map((t, i) => [t, 480 + wave(i, 60, 80)] as [number, number]), color: CHART_COLORS.violet },
  { name: 'p99', points: hours.map((t, i) => [t, 900 + wave(i, 100, 150)] as [number, number]), color: CHART_COLORS.orange, dashed: true },
]
const stats = [
  { label: 'Jobs', value: formatNumber(1534), icon: 'mdi-flag-checkered', change: 12, trend: done.map(p => p[1]), help: 'Finished jobs in the period.' },
  { label: 'Failed', value: '3.1', unit: '%', icon: 'mdi-alert-circle-outline', color: 'error', change: -8, goodWhenUp: false, trend: failed.map(p => p[1]) },
  { label: 'p95 response', value: formatMs(612), icon: 'mdi-timer-outline', color: 'brand-3', change: 4, goodWhenUp: false, trend: latency[1]!.points.map(p => p[1]) },
  { label: 'Cost so far', value: formatMoney(1234), icon: 'mdi-currency-usd', color: 'success', caption: 'This rental period' },
]
const heat = Array.from({ length: 7 }, (_, d) => Array.from({ length: 24 }, (_, h) => (d < 5 && h > 8 && h < 19 ? 20 + Math.round(Math.random() * 40) : Math.round(Math.random() * 6))))

const headers = [
  { title: 'Name', key: 'name' }, { title: 'Status', key: 'lifecycle' }, { title: 'Health', key: 'health' },
  { title: 'Endpoints', key: 'endpoints' }, { title: 'Id', key: 've_id', sortable: false },
]
const ves = ref([
  { ve_id: 've-7f3a91c2', name: 'ml-prod', lifecycle: 'Running', health: 'Healthy', endpoints: '3 / 3' },
  { ve_id: 've-12bd88e0', name: 'etl', lifecycle: 'Scaling', health: 'Degraded', endpoints: '2 / 4' },
  { ve_id: 've-99aa0b11', name: 'sandbox', lifecycle: 'Sleeping', health: null, endpoints: '0 / 1' },
  { ve_id: 've-5c0de000', name: 'old', lifecycle: 'Failed', health: 'Unavailable', endpoints: '0 / 2' },
])

const sampleError = new AxoApiError({ error: 'VEM_DOWN', error_code: 9525, message: "the VE manager can't be reached right now" }, 503)
function fail() {
  notify.error(new AxoApiError({ error: 'PLAN_LIMIT', error_code: 9515, message: 'the Basic plan allows at most 2 endpoints' }, 422), "Couldn't scale ml-prod")
}
async function remove() {
  const ok = await confirm({ title: 'Delete ml-prod?', danger: true, typeToConfirm: 'ml-prod', confirmText: 'Delete VE',
    consequences: ['Its 3 endpoints are removed', 'Its history is kept for 30 days, then deleted', 'The current bill closes now'] })
  if (ok) notify.success('Deleting ml-prod')
}
async function optimisticDemo() {
  const ve = ves.value[0]!
  const before = ve.lifecycle
  await optimistic({
    apply: () => { ve.lifecycle = 'Stopping' },
    rollback: () => { ve.lifecycle = before },
    run: () => new Promise((_, reject) => setTimeout(() => reject(new AxoApiError({ error: 'VE_STOPPED', error_code: 9522, message: 'demo: the server said no' }, 409)), 1500)),
    failure: "Couldn't stop ml-prod",
  })
}
</script>
