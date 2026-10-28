<template>
  <div>
    <AxoErrorAlert v-if="error" :error="error" title="The policies couldn't load" :retry="load" class="mb-4" />
    <v-alert v-if="asleep" type="info" variant="tonal" density="compact" class="mb-4">
      The VE is {{ ve?.lifecycle?.toLowerCase() }}: policies act only while it runs.
    </v-alert>

    <v-alert v-if="data && !data.allowed" type="info" variant="tonal" class="mb-4" icon="mdi-lock-outline">
      The {{ ve?.plan_id }} plan has no scaling policies.
      <template v-if="can('owner')" #append><v-btn variant="text" size="small" :to="`/ves/${id}`">Change plan</v-btn></template>
    </v-alert>
    <v-alert v-else-if="data?.paused_until" type="warning" variant="tonal" density="compact" class="mb-4" icon="mdi-pause-circle-outline">
      Paused until {{ formatDateTime(data.paused_until) }}<template v-if="data.pause_reason"> — {{ data.pause_reason }}</template>.
      <template v-if="can('admin')" #append><v-btn variant="text" size="small" @click="pause(0)">Resume now</v-btn></template>
    </v-alert>

    <AxoDataTable :headers="headers" :items="data?.policies ?? []" item-value="policy_id" :loading="!data && !error && !asleep"
                  title="Scaling policies" :searchable="false"
                  help="Rules that change the number of endpoints (or a function's runners) for you when a figure stays over or under a limit. A manual change of the target pauses them for a while.">
      <template #actions>
        <template v-if="can('admin') && data?.allowed">
          <v-menu v-if="!data?.paused_until && data?.policies?.length">
            <template #activator="{ props: menu }"><AxoBtn v-bind="menu" look="plain" prepend-icon="mdi-pause">Pause all</AxoBtn></template>
            <v-list density="compact">
              <v-list-item v-for="m in [30, 60, 240, 1440]" :key="m" :title="m < 60 ? `${m} minutes` : `${m / 60} hour${m === 60 ? '' : 's'}`" @click="pause(m)" />
            </v-list>
          </v-menu>
          <AxoBtn prepend-icon="mdi-plus" @click="edit(undefined)">New policy</AxoBtn>
        </template>
      </template>
      <template #empty>
        <AxoEmptyState icon="mdi-auto-fix" title="No policies"
                       text="Without policies the VE keeps the number of endpoints you set. Add one to grow when jobs wait and shrink when it's quiet." />
      </template>
      <template #item.name="{ item }">
        <div class="py-2">
          <div class="font-weight-medium">{{ item.name }}</div>
          <div class="text-caption axo-muted">{{ item.kind === 'runner' ? `Runners of ${fname(item.function_id)} v${item.function_version}` : 'Endpoints' }}</div>
        </div>
      </template>
      <template #item.condition="{ item }">
        <span class="axo-mono text-body-2">{{ item.condition }}</span>
        <span class="text-caption axo-muted"> for {{ item.duration_s }} s</span>
      </template>
      <template #item.action="{ item }">
        {{ item.delta > 0 ? `+${item.delta}` : item.delta }} · {{ item.min_value }}–{{ item.max_value }}
      </template>
      <template #item.enabled="{ item }">
        <div class="d-flex align-center ga-1">
          <v-switch :model-value="item.enabled" :disabled="!can('admin')" color="primary" inset hide-details density="compact"
                    @update:model-value="patch(item, { enabled: !!$event })" @click.stop />
          <v-chip v-if="item.dry_run" size="x-small" variant="tonal" color="info">dry run</v-chip>
        </div>
      </template>
      <template #item.tools="{ item }">
        <div v-if="can('admin')" class="d-flex justify-end">
          <v-btn icon="mdi-pencil-outline" variant="text" size="small" aria-label="Change" @click.stop="edit(item)" />
          <v-btn icon="mdi-delete-outline" variant="text" size="small" aria-label="Delete" @click.stop="remove(item)" />
        </div>
      </template>
    </AxoDataTable>

    <AxoPanel title="What the policies did" icon="mdi-history" class="mt-6" flush
              help="Every change a policy made (or, in dry run, would have made), newest first, with the figure that caused it.">
      <v-table class="bg-transparent">
        <thead><tr><th>When</th><th>Policy</th><th>Change</th><th>Why</th></tr></thead>
        <tbody>
          <tr v-for="(d, i) in decisions" :key="i">
            <td class="text-no-wrap">{{ timeAgo(Date.parse(d.created_at) / 1000) }}</td>
            <td>{{ d.policy_name }}<v-chip v-if="d.dry_run" size="x-small" variant="tonal" color="info" class="ml-1">dry run</v-chip></td>
            <td class="text-no-wrap">{{ d.before }} → {{ d.after }}</td>
            <td class="text-body-2">{{ d.reason }}</td>
          </tr>
          <tr v-if="!decisions.length"><td colspan="4" class="text-center axo-muted py-6">No decisions yet.</td></tr>
        </tbody>
      </v-table>
    </AxoPanel>

    <VePolicyDialog v-model="dialog" :policy="editing" :templates="templates" :max-endpoints="maxEndpoints" @saved="load" />
  </div>
</template>

<script setup lang="ts">
// Scaling policies (the VEM's /policies): list, enable / disable, new from
// a template, change, delete, pause all for a while, and the decisions
// they made. Changing them is for Admins.
const { id, ve, can, vemUp, api } = useCurrentVe()
const notify = useNotifier()
const confirmDialog = useConfirm()

const data = ref<any>(null)
const decisions = ref<any[]>([])
const templates = ref<any[]>([])
const names = ref<Record<string, string>>({})
const maxEndpoints = ref(1)
const error = ref<unknown>(null)
const asleep = computed(() => !vemUp.value)

const headers = [
  { title: 'Policy', key: 'name' },
  { title: 'When', key: 'condition', sortable: false },
  { title: 'Change · limits', key: 'action', sortable: false },
  { title: 'On', key: 'enabled', sortable: false },
  { title: '', key: 'tools', sortable: false, align: 'end' as const },
]

async function load() {
  if (asleep.value) return
  error.value = null
  try {
    const [d, dec, t, v, fns] = await Promise.all([
      api('/policies'), api('/policies/decisions?limit=50'), api('/policies/templates'), api('/ve'),
      api<Array<{ function_id: string; name: string }>>('/functions').catch(() => []),
    ])
    data.value = d
    decisions.value = dec
    templates.value = t
    maxEndpoints.value = v.plan_limits?.max_endpoints ?? 1
    names.value = Object.fromEntries(fns.map(f => [f.function_id, f.name]))
  } catch (err) { error.value = err }
}
const fname = (fid?: string) => (fid ? names.value[fid] ?? `${fid.slice(0, 10)}…` : '—')
watch(id, load)
watch(asleep, (now) => { if (!now) load() })
onMounted(load)

const dialog = ref(false)
const editing = ref<any>(undefined)
function edit(policy: any) { editing.value = policy; dialog.value = true }

async function patch(policy: any, change: Record<string, unknown>) {
  const before = { ...policy }
  Object.assign(policy, change)
  try { await api(`/policies/${policy.policy_id}`, { method: 'PATCH', body: change }) }
  catch (err) { Object.assign(policy, before); notify.error(err, "The policy couldn't be changed") }
}

async function remove(policy: any) {
  if (!await confirmDialog.confirm({ title: `Delete ${policy.name}?`, danger: true, confirmText: 'Delete',
    text: 'The target stays where the policy left it.' })) return
  try {
    await api(`/policies/${policy.policy_id}`, { method: 'DELETE' })
    data.value.policies = data.value.policies.filter((p: any) => p.policy_id !== policy.policy_id)
    notify.success(`${policy.name} deleted`)
  } catch (err) { notify.error(err, "The policy couldn't be deleted") }
}

async function pause(minutes: number) {
  try {
    const answer = await api('/policies/pause', { method: 'POST', body: { minutes } })
    data.value.paused_until = answer.paused_until
    if (!minutes) data.value.pause_reason = null
    notify.success(minutes ? 'Policies paused' : 'Policies act again')
  } catch (err) { notify.error(err, "The policies couldn't be paused") }
}
</script>
