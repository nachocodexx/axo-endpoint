<template>
  <AxoPanel title="Kept data" icon="mdi-database-clock-outline" :class="$attrs.class" stacked
            help="What the VE keeps besides its endpoints: every job's result (kept as long as its keep time) and active objects' saved state. Results count as the owner's storage; object state has its own price.">
    <AxoSkeleton v-if="!data && !error" :lines="2" />
    <div v-else-if="error" class="text-body-2 axo-muted">Couldn't load what it keeps.</div>
    <template v-else-if="data">
      <div v-for="row in rows" :key="row.key" class="d-flex align-center ga-3 py-1">
        <v-icon size="20" class="axo-muted">{{ row.icon }}</v-icon>
        <div class="flex-grow-1">
          <div class="text-body-2">{{ row.title }}</div>
          <div class="text-caption axo-muted">{{ data[row.key].at ? `as of ${timeAgo(data[row.key].at)}` : 'nothing measured yet' }}</div>
        </div>
        <div class="text-end">
          <div class="font-weight-medium">{{ formatBytes(data[row.key].bytes) }}</div>
          <div v-if="data[row.key].period_ucents != null" class="text-caption axo-muted">
            {{ formatUcents(data[row.key].period_ucents) }} this period</div>
        </div>
      </div>
      <template v-if="data.platform_buckets?.length">
        <v-divider class="my-2" />
        <div class="d-flex align-center ga-1 text-caption axo-muted mb-1">
          Platform buckets · managed by the platform: read-only
          <AxoHelp title="Platform buckets" text="Buckets the platform keeps for this VE: its code (to start it again after a stop), every job's result, and objects' big values. People can't open or change them; they're never in Buckets." />
        </div>
        <div v-for="b in data.platform_buckets" :key="b.bucket" class="d-flex align-center ga-3 py-1">
          <v-icon size="20" class="axo-muted">{{ PLATFORM[b.kind]?.icon ?? 'mdi-bucket-outline' }}</v-icon>
          <div class="flex-grow-1 min-w-0">
            <div class="text-body-2">{{ PLATFORM[b.kind]?.title ?? b.kind }}</div>
            <div class="text-caption axo-muted axo-mono text-truncate">{{ b.bucket }}</div>
          </div>
          <div class="text-end">
            <div class="font-weight-medium">{{ formatBytes(b.bytes) }}</div>
            <div class="text-caption axo-muted">{{ formatNumber(b.files) }} file{{ b.files === 1 ? '' : 's' }}</div>
          </div>
        </div>
      </template>
    </template>
  </AxoPanel>
</template>

<script setup lang="ts">
// Job results and active object state the VE keeps: sizes for everyone with
// a role in it, what they cost this rental period for its Owner / Admins;
// then the VE's three platform buckets (read-only) with storage's figures.
defineOptions({ inheritAttrs: false })
const { id, cp } = useCurrentVe()

interface Part { bytes: number; at: number | null; period_ucents?: number }
interface PlatformBucket { kind: string; bucket: string; bytes: number; files: number; last_change?: unknown }
const { data, error } = useLazyAsyncData(() => `ve-stored:${id.value}`,
  () => cp<{ results: Part; object_state: Part; platform_buckets?: PlatformBucket[] | null }>('/stored'), { server: false, watch: [id] })
const PLATFORM: Record<string, { title: string; icon: string }> = {
  functions: { title: 'Code (functions and classes)', icon: 'mdi-code-braces' },
  results: { title: 'Job results', icon: 'mdi-text-box-check-outline' },
  objects: { title: "Objects' big values", icon: 'mdi-cube-outline' },
}
const rows = [
  { key: 'results' as const, title: 'Job results', icon: 'mdi-text-box-check-outline' },
  { key: 'object_state' as const, title: 'Object state', icon: 'mdi-cube-scan' },
]
</script>
