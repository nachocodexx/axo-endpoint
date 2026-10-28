<template>
  <div>
    <div class="text-body-2 axo-muted mb-3">
      Every change of the code is a new version; a job runs the version it was started with. The <b>default</b> one runs when none
      is named (the Run form, a choreography step without a version): a new version becomes the default<template v-if="can('developer')">,
      and clicking a version makes it the default</template>. Deleting a version stops new jobs on it (its jobs and their results stay).
      Admins delete any version, Developers only the ones they registered.
    </div>
    <AxoDataTable :headers="headers" :items="versions" item-value="version" :searchable="false" :sort-by="[{ key: 'version', order: 'desc' }]"
                  :clickable="settable" empty-icon="mdi-source-branch" empty-title="No versions" @row="makeDefault">
      <template #item.default="{ item }">
        <v-progress-circular v-if="busy === `m${item.version}`" indeterminate size="18" width="2" color="primary" />
        <v-icon v-else-if="item.version === row?.defaultVersion && !item.deleted_at" color="primary" size="20"
                :aria-label="`v${item.version} is the default`">mdi-radiobox-marked</v-icon>
        <v-icon v-else-if="!item.deleted_at" :color="can('developer') ? undefined : 'transparent'" size="20" class="axo-muted"
                :aria-label="can('developer') ? `Make v${item.version} the default` : undefined">mdi-radiobox-blank</v-icon>
      </template>
      <template #item.version="{ item }">
        <span class="font-weight-medium">v{{ item.version }}</span>
        <v-chip v-if="item.version === row?.defaultVersion && !item.deleted_at" size="x-small" variant="flat" color="primary" class="ml-2">default</v-chip>
        <v-chip v-if="item.version === row?.latest.version && !item.deleted_at" size="x-small" variant="tonal" color="primary" class="ml-2">newest</v-chip>
      </template>
      <template #item.state="{ item }"><AxoStatusChip kind="function" :value="functionState(item)" /></template>
      <template #item.runtime="{ item }"><span class="text-body-2">{{ runtimeLabel(item.runtime_spec) }}</span></template>
      <template #item.by="{ item }"><span class="text-body-2">{{ item.registered_by_name ?? '—' }}</span></template>
      <template #item.at="{ item }">
        <span class="text-body-2" :title="formatExactTime(ts(item.registered_at))">{{ item.registered_at ? timeAgo(ts(item.registered_at)) : '—' }}</span>
      </template>
      <template #item.hash="{ item }"><span class="axo-mono text-caption">{{ item.code_hash?.slice(0, 10) }}</span></template>
      <template #item.actions="{ item }">
        <div class="d-flex justify-end ga-1" @click.stop>
          <v-btn size="small" variant="text" class="text-none" :to="`/ves/${id}/functions/${functionId}/code?version=${item.version}`">Code</v-btn>
          <v-btn v-if="can('developer') && !item.deleted_at" size="small" variant="text" class="text-none"
                 :to="`/ves/${id}/functions/${functionId}/run?version=${item.version}`">Run</v-btn>
          <v-btn v-if="!item.deleted_at && mayDelete(item)" size="small" variant="text" color="error" class="text-none"
                 :loading="busy === `d${item.version}`" @click="remove(item)">Delete</v-btn>
          <v-btn v-else-if="item.deleted_at && mayDelete(item)" size="small" variant="text" color="error" class="text-none"
                 :loading="busy === `p${item.version}`" @click="purge(item)">Remove for good</v-btn>
        </div>
      </template>
    </AxoDataTable>
  </div>
</template>

<script setup lang="ts">
import type { FunctionVersion } from '~/utils/functions'

// The function's versions: who registered each, how it runs, which one is the
// default (Developers make one the default by clicking its row; Undo in the
// snackbar), and delete (stop new jobs) / remove for good (a deleted one,
// with its history).
const { id, can, api } = useCurrentVe()
const { functionId, versions, row, mayDelete, functions } = useCurrentFunction()
const { confirm } = useConfirm()
const notifier = useNotifier()
const busy = ref<string | null>(null)

const WIDE = { headerProps: { class: 'd-none d-md-table-cell' }, cellProps: { class: 'd-none d-md-table-cell' } }
const headers = [
  { title: 'Default', key: 'default', sortable: false, width: 72, align: 'center' as const },
  { title: 'Version', key: 'version', sortable: true },
  { title: 'State', key: 'state', sortable: false },
  { title: 'Runs as', key: 'runtime', sortable: false, ...WIDE },
  { title: 'Registered by', key: 'by', sortable: false, ...WIDE },
  { title: 'Registered', key: 'at', sortable: false },
  { title: 'Hash', key: 'hash', sortable: false, headerProps: { class: 'd-none d-lg-table-cell' }, cellProps: { class: 'd-none d-lg-table-cell' } },
  { title: '', key: 'actions', sortable: false, align: 'end' as const },
]

const settable = (v: FunctionVersion) => can('developer') && !v.deleted_at && v.version !== row.value?.defaultVersion

async function makeDefault(v: FunctionVersion, undoTo?: number) {
  if (!settable(v) || busy.value) return
  const before = row.value?.defaultVersion
  busy.value = `m${v.version}`
  try {
    await api(`/functions/${functionId.value}/default`, { method: 'PUT', body: { version: v.version } })
    await functions.load(true)
    if (undoTo === undefined && before != null) {
      notifier.success(`v${v.version} is now the default`, { action: { text: 'Undo', run: () => {
        const back = versions.value.find(x => x.version === before)
        if (back) makeDefault(back, before)
      } } })
    } else if (undoTo !== undefined) notifier.info(`v${v.version} is the default again`)
  } catch (err) { notifier.error(err, `v${v.version} couldn't be made the default`) } finally { busy.value = null }
}

async function remove(v: FunctionVersion) {
  const ok = await confirm({ title: `Delete v${v.version}?`, text: 'New jobs can no longer run it. Its jobs and their results stay.',
    confirmText: 'Delete', danger: true })
  if (!ok) return
  busy.value = `d${v.version}`
  try {
    await api(`/functions/${functionId.value}/${v.version}`, { method: 'DELETE' })
    notifier.success(`v${v.version} deleted`)
    await functions.load(true)
  } catch (err) { notifier.error(err, `v${v.version} couldn't be deleted`) } finally { busy.value = null }
}
async function purge(v: FunctionVersion) {
  const ok = await confirm({ title: `Remove v${v.version} for good?`, text: 'Its record and history go too. This can’t be undone.',
    confirmText: 'Remove for good', danger: true, typeToConfirm: `v${v.version}` })
  if (!ok) return
  busy.value = `p${v.version}`
  try {
    await api(`/functions/${functionId.value}/${v.version}/purge`, { method: 'DELETE' })
    notifier.success(`v${v.version} removed`)
    await functions.load(true)
  } catch (err) { notifier.error(err, `v${v.version} couldn't be removed`) } finally { busy.value = null }
}
</script>
