<template>
  <div>
    <AxoErrorAlert v-if="error" :error="error" title="This bucket couldn't load" :retry="load" class="mb-4" />

    <template v-if="bucket">
      <AxoPageHeader :title="bucket.name" icon="mdi-database-outline"
                     :crumbs="[{ title: 'Buckets', to: '/buckets' }, { title: bucket.name }]">
        <template #title-append>
          <v-chip v-for="a in bucket.aliases" :key="a" size="x-small" variant="tonal" prepend-icon="mdi-link-variant" class="ml-1">{{ a }}</v-chip>
        </template>
        <template #subtitle>
          {{ bucket.owner.kind === 'team' ? `Team ${bucket.owner.name}` : bucket.relation === 'mine' ? 'Yours' : `Shared by ${bucket.owner.name}` }}
          · you are {{ bucketRoleTitle(bucket.my_role) }}
          <template v-if="bucket.files != null"> · {{ formatNumber(bucket.files, false) }} file{{ bucket.files === 1 ? '' : 's' }}</template>
        </template>
        <template #actions>
          <div class="axo-used text-end">
            <div class="text-body-2"><span class="font-weight-medium">{{ bucket.used_bytes == null ? '—' : formatBytes(bucket.used_bytes) }}</span>
              <span class="axo-muted"> of {{ formatBytes(bucket.quota_bytes) }}</span></div>
            <v-progress-linear v-if="usedPct(bucket) != null" :model-value="usedPct(bucket)!" height="5" rounded class="mt-1"
                               :color="usedPct(bucket)! >= 90 ? 'error' : usedPct(bucket)! >= 75 ? 'warning' : 'primary'" />
          </div>
        </template>
      </AxoPageHeader>

      <v-tabs :model-value="tab" color="primary" class="mb-6 axo-bucket-tabs" show-arrows>
        <v-tab v-for="t in tabs" :key="t.path" :value="t.path" :to="bucketLink(bucket.name, t.path)" :exact="!t.path" :prepend-icon="t.icon">
          {{ t.title }}
        </v-tab>
      </v-tabs>

      <NuxtPage />
    </template>
    <AxoSkeleton v-else-if="!error" :lines="8" />
  </div>
</template>

<script setup lang="ts">
// The frame of one bucket's pages: name, aliases, owner, your role, how
// full it is, and its tabs (Files, Access; Settings for the Owner).
const { name, bucket, can, reload } = useCurrentBucket()
const route = useRoute()
useHead(() => ({ title: `${bucket.value?.name ?? 'Bucket'} · axo` }))

const tabs = computed(() => [
  { path: '', title: 'Files', icon: 'mdi-file-multiple-outline' },
  { path: 'access', title: 'Access', icon: 'mdi-account-key-outline' },
  ...(can('owner') ? [{ path: 'settings', title: 'Settings', icon: 'mdi-tune-variant' }] : []),
])
const tab = computed(() => String(route.path.split('/')[4] ?? ''))

// Loaded before the first render (on the server too), so the server and the
// browser draw the same page.
const { error, refresh: load } = await useAsyncData(() => `bucket:${name.value}`, () => reload(), { watch: [name] })
</script>

<style scoped>
.axo-bucket-tabs { border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
.axo-used { min-width: 180px; }
</style>
