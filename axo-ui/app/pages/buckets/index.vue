<template>
  <div>
    <AxoPageHeader title="Buckets" icon="mdi-database-outline"
                   subtitle="Files your functions read and write."
                   help="A bucket keeps files. It belongs to you or to a team -- not to a VE -- so the functions of any VE you work in can use it, and it stays when a VE is deleted. It is billed on the storage bill of its owner.">
      <template #actions>
        <AxoBtn prepend-icon="mdi-plus" @click="createOpen = true">New bucket</AxoBtn>
      </template>
    </AxoPageHeader>

    <AxoErrorAlert v-if="error" :error="error" title="Your buckets couldn't load" :retry="load" class="mb-4" />

    <v-chip-group v-model="filter" mandatory selected-class="text-primary" class="mb-2">
      <v-chip v-for="f in FILTERS" :key="f.value" :value="f.value" variant="outlined" size="small"
              :prepend-icon="f.value === 'deleted' ? 'mdi-delete-restore' : f.value === 've' ? 'mdi-cube-outline' : undefined">
        {{ f.title }}<span v-if="counts[f.value] != null" class="ml-1 axo-muted">· {{ counts[f.value] }}</span>
      </v-chip>
    </v-chip-group>

    <VeBucketsTab v-if="filter === 've'" />

    <AxoDataTable v-else-if="filter !== 'deleted'" :headers="headers" :items="shown" item-value="bucket_id" :loading="loading"
                  :title="FILTERS.find(f => f.value === filter)!.title" search-placeholder="Search by name or alias"
                  :row-to="(b) => bucketLink(b.name)" :sort-by="[{ key: 'name', order: 'asc' }]">
      <template #empty>
        <AxoEmptyState icon="mdi-database-outline" :title="filter === 'all' ? 'No buckets yet' : 'None here'"
                       :text="filter === 'all' ? 'A bucket keeps files that your functions read and write. Create one, or ask someone to share theirs.' : 'Nothing matches this filter.'">
          <AxoBtn v-if="filter === 'all'" prepend-icon="mdi-plus" class="mt-2" @click="createOpen = true">New bucket</AxoBtn>
        </AxoEmptyState>
      </template>
      <template #item.name="{ item }">
        <div class="py-2">
          <div class="font-weight-medium">{{ item.name }}</div>
          <div v-if="item.aliases.length" class="d-flex flex-wrap ga-1 mt-1">
            <v-chip v-for="a in item.aliases" :key="a" size="x-small" variant="tonal" prepend-icon="mdi-link-variant">{{ a }}</v-chip>
          </div>
        </div>
      </template>
      <template #item.owner="{ item }">
        <span>{{ item.owner.kind === 'team' ? `Team ${item.owner.name}` : item.relation === 'mine' ? 'You' : item.owner.name }}</span>
        <div v-if="item.relation === 'shared'" class="text-caption axo-muted">Shared with you</div>
      </template>
      <template #item.my_role="{ item }">{{ bucketRoleTitle(item.my_role) }}</template>
      <template #item.used_bytes="{ item }">
        <div style="min-width: 150px">
          <div class="text-body-2">{{ item.used_bytes == null ? '—' : formatBytes(item.used_bytes) }}
            <span class="text-caption axo-muted">of {{ formatBytes(item.quota_bytes) }}</span></div>
          <v-progress-linear v-if="usedPct(item) != null" :model-value="usedPct(item)!" height="4" rounded class="mt-1"
                             :color="usedPct(item)! >= 90 ? 'error' : usedPct(item)! >= 75 ? 'warning' : 'primary'" />
        </div>
      </template>
      <template #item.files="{ item }">{{ item.files == null ? '—' : formatNumber(item.files, false) }}</template>
      <template #item.last_change="{ item }">
        <span v-if="item.last_change" :title="formatDateTime(item.last_change)">{{ timeAgo(item.last_change) }}</span>
        <span v-else class="axo-muted">—</span>
      </template>
      <template #item.created_at="{ item }">{{ formatDate(item.created_at) }}</template>
    </AxoDataTable>

    <AxoDataTable v-else :headers="deletedHeaders" :items="deleted" item-value="bucket_id" :loading="loading"
                  title="Deleted" help="Buckets you deleted, kept for 30 days: restoring brings back their files, names and who had access."
                  :pending="(b) => busy === b.bucket_id">
      <template #empty>
        <AxoEmptyState icon="mdi-delete-restore" title="Nothing deleted"
                       text="A bucket you delete waits here for 30 days before its files are removed for good." />
      </template>
      <template #item.name="{ item }"><span class="font-weight-medium">{{ item.name }}</span></template>
      <template #item.deleted_at="{ item }">
        <span :title="formatDateTime(item.deleted_at)">{{ timeAgo(item.deleted_at) }}</span>
      </template>
      <template #item.purge_after="{ item }">
        <span :class="daysLeft(item.purge_after) <= 3 ? 'text-error' : ''">
          in {{ daysLeft(item.purge_after) }} day{{ daysLeft(item.purge_after) === 1 ? '' : 's' }}</span>
        <div class="text-caption axo-muted">{{ formatDate(item.purge_after) }}</div>
      </template>
      <template #item.tools="{ item }">
        <div class="d-flex justify-end ga-1">
          <v-btn size="small" variant="tonal" color="primary" class="text-none" prepend-icon="mdi-restore"
                 :disabled="!!busy" @click="restore(item)">Restore</v-btn>
          <v-btn size="small" variant="text" color="error" class="text-none" :disabled="!!busy"
                 @click="purge(item)">Delete permanently</v-btn>
        </div>
      </template>
    </AxoDataTable>

    <BucketCreateDialog v-model="createOpen" @created="(b) => navigateTo(bucketLink(b.name))" />
  </div>
</template>

<script setup lang="ts">
import type { Bucket } from '../../utils/buckets'

// Every bucket the person reaches (theirs, their teams', shared with them)
// with what it holds, and the deleted ones they can still restore.
useHead({ title: 'Buckets · axo' })
const { apiFetch } = useApi()
const notify = useNotifier()
const confirmDialog = useConfirm()
const route = useRoute()

const FILTERS = [
  { value: 'all', title: 'All buckets' }, { value: 'mine', title: 'Mine' }, { value: 'team', title: 'Team' },
  { value: 'shared', title: 'Shared with me' }, { value: 've', title: 'VE buckets' }, { value: 'deleted', title: 'Deleted' },
] as const
type Filter = typeof FILTERS[number]['value']
const filter = ref<Filter>((FILTERS.find(f => f.value === route.query.show)?.value ?? 'all') as Filter)
watch(filter, (f) => {
  navigateTo({ query: f === 'all' ? {} : { show: f } }, { replace: true })
  if (f === 'deleted') loadDeleted()
})

const buckets = ref<Bucket[]>([])
const deleted = ref<Bucket[]>([])
const loading = ref(true)
const error = ref<unknown>(null)

async function load() {
  error.value = null
  try {
    buckets.value = await apiFetch<Bucket[]>('/buckets')
    if (filter.value === 'deleted') await loadDeleted()
  } catch (err) { error.value = err } finally { loading.value = false }
}
async function loadDeleted() {
  try { deleted.value = await apiFetch<Bucket[]>('/buckets', { query: { deleted: true } }) } catch (err) { error.value = err }
}
onMounted(load)

const shown = computed(() => (filter.value === 'all' ? buckets.value : buckets.value.filter(b => b.relation === filter.value))
  .map(b => ({ ...b, search: [b.name, ...b.aliases].join(' ') })))
const counts = computed<Record<Filter, number | null>>(() => ({
  all: buckets.value.length, mine: buckets.value.filter(b => b.relation === 'mine').length,
  team: buckets.value.filter(b => b.relation === 'team').length,
  shared: buckets.value.filter(b => b.relation === 'shared').length, ve: null, deleted: null,
}))

const headers = [
  { title: 'Name', key: 'name', value: (b: any) => b.search },
  { title: 'Owner', key: 'owner', value: (b: Bucket) => b.owner.name },
  { title: 'Your role', key: 'my_role' },
  { title: 'Used', key: 'used_bytes' },
  { title: 'Files', key: 'files', align: 'end' as const },
  { title: 'Last change', key: 'last_change', value: (b: Bucket) => b.last_change ?? 0 },
  { title: 'Created', key: 'created_at' },
]
const deletedHeaders = [
  { title: 'Name', key: 'name' }, { title: 'Deleted', key: 'deleted_at' },
  { title: 'Removed for good', key: 'purge_after' }, { title: '', key: 'tools', sortable: false, align: 'end' as const },
]

const createOpen = ref(false)
const busy = ref<string | null>(null)

async function restore(b: Bucket) {
  busy.value = b.bucket_id
  try {
    await apiFetch(`/deleted-buckets/${b.bucket_id}/restore`, { method: 'POST' })
    deleted.value = deleted.value.filter(d => d.bucket_id !== b.bucket_id)
    notify.success(`${b.name} is back, with its files and who had access`, { to: bucketLink(b.name) })
    buckets.value = await apiFetch<Bucket[]>('/buckets')
  } catch (err) { notify.error(err, `${b.name} couldn't be restored`) } finally { busy.value = null }
}

async function purge(b: Bucket) {
  if (!await confirmDialog.confirm({
    title: `Delete ${b.name} permanently?`, danger: true, confirmText: 'Delete permanently', typeToConfirm: b.name,
    text: "Its files are removed now instead of in " + `${daysLeft(b.purge_after)} days. This can't be undone.`,
    consequences: ['Its name and aliases become free for anyone to use.'],
  })) return
  busy.value = b.bucket_id
  try {
    await apiFetch(`/deleted-buckets/${b.bucket_id}`, { method: 'DELETE' })
    deleted.value = deleted.value.filter(d => d.bucket_id !== b.bucket_id)
    notify.success(`${b.name} was deleted permanently`)
  } catch (err) { notify.error(err, `${b.name} couldn't be deleted`) } finally { busy.value = null }
}
</script>
