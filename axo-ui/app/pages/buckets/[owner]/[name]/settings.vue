<template>
  <div class="d-flex flex-column ga-6">
    <template v-if="bucket">
    <AxoPanel title="Size limit" icon="mdi-gauge"
              help="The most this bucket may hold. Writes that don't fit are refused; you pay for what it holds, not for the limit.">
      <div class="d-flex align-center flex-wrap ga-4">
        <div style="min-width: 220px">
          <div class="text-body-2"><span class="font-weight-medium">{{ formatBytes(bucket.used_bytes ?? 0) }}</span>
            <span class="axo-muted"> used of {{ formatBytes(bucket.quota_bytes) }}</span></div>
          <v-progress-linear :model-value="usedPct(bucket) ?? 0" height="6" rounded class="mt-2"
                             :color="(usedPct(bucket) ?? 0) >= 90 ? 'error' : 'primary'" />
        </div>
        <v-spacer />
        <v-text-field v-model.number="limit" type="number" min="1" label="Limit" density="compact" hide-details style="max-width: 140px" />
        <v-select v-model="unit" :items="SIZE_UNITS" density="compact" hide-details style="max-width: 100px" />
        <AxoBtn :loading="saving === 'quota'" :disabled="!(limit > 0) || newLimit === bucket.quota_bytes" @click="saveQuota">Save</AxoBtn>
      </div>
      <v-alert v-if="limit > 0 && newLimit < (bucket.used_bytes ?? 0)" type="warning" variant="tonal" density="compact" class="mt-4">
        That is less than it holds now ({{ formatBytes(bucket.used_bytes ?? 0) }}). Nothing is deleted, but new writes are refused
        until enough files are deleted.
      </v-alert>
    </AxoPanel>

    <AxoPanel title="Aliases" icon="mdi-link-variant"
              help="An alias is another name for this bucket. Code can use the alias; later you can move the alias to another bucket (say from sales-2024 to sales-2025) without changing any code or moving any data.">
      <template #actions>
        <AxoBtn look="plain" prepend-icon="mdi-plus" @click="aliasOpen = true">Add alias</AxoBtn>
      </template>
      <div v-if="!bucket.aliases.length" class="text-body-2 axo-muted">No aliases. Its only name is {{ bucket.name }}.</div>
      <v-list v-else density="compact" class="py-0">
        <v-list-item v-for="a in bucket.aliases" :key="a" prepend-icon="mdi-link-variant" :title="a" class="px-0">
          <template #append>
            <v-btn size="small" variant="text" class="text-none" @click="startMove(a)">Move to another bucket…</v-btn>
            <v-btn size="small" variant="text" color="error" class="text-none" @click="removeAlias(a)">Remove</v-btn>
          </template>
        </v-list-item>
      </v-list>
    </AxoPanel>

    <AxoPanel title="Transfer ownership" icon="mdi-account-switch-outline"
              help="Give the bucket to another person or team. They must accept; its files never move, and its current name stays as an alias so nothing breaks.">
      <div class="d-flex align-center flex-wrap ga-3">
        <div class="text-body-2 axo-muted">Today it belongs to {{ bucket.owner.kind === 'team' ? `the team ${bucket.owner.name}` : bucket.owner.name }}.</div>
        <v-spacer />
        <AxoBtn look="plain" prepend-icon="mdi-account-switch-outline" @click="transferOpen = true">Transfer…</AxoBtn>
      </div>
    </AxoPanel>

    <AxoPanel title="Delete this bucket" icon="mdi-alert-outline" class="axo-danger">
      <div class="d-flex align-center flex-wrap ga-3">
        <div class="text-body-2">It disappears for everyone at once. Its files are kept for 30 days, and you can restore it
          (with who had access) from Buckets → Deleted until then.</div>
        <v-spacer />
        <AxoBtn look="danger" prepend-icon="mdi-delete-outline" @click="remove">Delete</AxoBtn>
      </div>
    </AxoPanel>

    <v-dialog v-model="aliasOpen" max-width="480">
      <v-card rounded="lg">
        <v-card-title class="pt-5 px-6">Add an alias</v-card-title>
        <v-card-text class="px-6">
          <div class="d-flex ga-3 align-start">
            <v-select v-model="aliasOwner" :items="owners" label="Owner" style="max-width: 160px" />
            <v-text-field v-model="aliasName" label="Name" autofocus :prefix="`${aliasOwner}/`" @keyup.enter="addAlias" />
          </div>
          <div class="text-caption axo-muted">It is a name like any bucket's, so it must be free.</div>
        </v-card-text>
        <v-card-actions class="px-6 pb-5">
          <v-spacer />
          <AxoBtn look="plain" variant="text" @click="aliasOpen = false">Cancel</AxoBtn>
          <AxoBtn :loading="saving === 'alias'" :disabled="!aliasName.trim()" @click="addAlias">Add alias</AxoBtn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="moveOpen" max-width="480">
      <v-card rounded="lg">
        <v-card-title class="pt-5 px-6">Move {{ moving }}</v-card-title>
        <v-card-text class="px-6">
          <v-select v-model="moveTarget" :items="targets" label="To the bucket" no-data-text="You own no other bucket" />
          <div class="text-caption axo-muted">From then on, code using {{ moving }} reads and writes that bucket.</div>
        </v-card-text>
        <v-card-actions class="px-6 pb-5">
          <v-spacer />
          <AxoBtn look="plain" variant="text" @click="moveOpen = false">Cancel</AxoBtn>
          <AxoBtn :loading="saving === 'move'" :disabled="!moveTarget" @click="moveAlias">Move alias</AxoBtn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <TransferDialog v-model="transferOpen" kind="bucket" :target="name" :target-name="bucket.name"
                    :current-owner="bucket.owner.kind === 'team' ? bucket.owner.name : undefined" />
    </template>
  </div>
</template>

<script setup lang="ts">
import type { Bucket, SizeUnit } from '../../../../utils/buckets'

// The Owner's settings: size limit, aliases (add / move / remove), handing
// the bucket to someone else, and deleting it (restorable for 30 days).
const { name, bucket, can, call, reload } = useCurrentBucket()
const { apiFetch } = useApi()
const notify = useNotifier()
const confirmDialog = useConfirm()
const me = useMeStore()

watchEffect(() => { if (bucket.value && !can('owner')) navigateTo(bucketLink(bucket.value.name), { replace: true }) })

const saving = ref<string | null>(null)

// ── size limit ─────────────────────────────────────────────────────────────
const limit = ref(10)
const unit = ref<SizeUnit>('GB')
watch(() => bucket.value?.quota_bytes, (q) => {
  if (!q) return
  const s = splitSize(q)
  limit.value = s.value
  unit.value = s.unit
}, { immediate: true })
const newLimit = computed(() => joinSize(limit.value || 0, unit.value))

async function saveQuota() {
  saving.value = 'quota'
  try {
    await call('', { method: 'PATCH', body: { quota_bytes: newLimit.value } })
    await reload()
    notify.success(`The limit is now ${formatBytes(newLimit.value)}`)
  } catch (err) { notify.error(err, "The limit couldn't be changed") } finally { saving.value = null }
}

// ── aliases ────────────────────────────────────────────────────────────────
const aliasOpen = ref(false)
const aliasName = ref('')
const aliasOwner = ref('')
const teams = ref<{ name: string; members: { user_id: string; role: string }[] }[]>([])
const owners = computed(() => [me.me?.username ?? '', ...teams.value
  .filter(t => t.members.some(m => m.user_id === me.me?.user_id && ['owner', 'admin'].includes(m.role)))
  .map(t => t.name)].filter(Boolean))
watch(aliasOpen, async (open) => {
  if (!open) return
  aliasName.value = ''
  aliasOwner.value = bucket.value?.owner.name ?? me.me?.username ?? ''
  try { teams.value = await apiFetch('/teams') } catch { teams.value = [] }
})

async function addAlias() {
  const alias = `${aliasOwner.value}/${aliasName.value.trim().toLowerCase()}`
  saving.value = 'alias'
  try {
    await call('/aliases', { method: 'POST', body: { alias } })
    await reload()
    aliasOpen.value = false
    notify.success(`${alias} now points at ${name.value}`)
  } catch (err) { notify.error(err, "The alias couldn't be added") } finally { saving.value = null }
}

const moveOpen = ref(false)
const moving = ref('')
const moveTarget = ref<string | null>(null)
const owned = ref<Bucket[]>([])
const targets = computed(() => owned.value.filter(b => b.name !== name.value && b.my_role === 'owner').map(b => b.name))
async function startMove(alias: string) {
  moving.value = alias
  moveTarget.value = null
  moveOpen.value = true
  try { owned.value = await apiFetch<Bucket[]>('/buckets') } catch { owned.value = [] }
}
async function moveAlias() {
  saving.value = 'move'
  try {
    await apiFetch(`/aliases/${moving.value}`, { method: 'PUT', body: { target: moveTarget.value } })
    await reload()
    moveOpen.value = false
    notify.success(`${moving.value} now points at ${moveTarget.value}`)
  } catch (err) { notify.error(err, "The alias couldn't be moved") } finally { saving.value = null }
}

async function removeAlias(alias: string) {
  if (!await confirmDialog.confirm({
    title: `Remove the alias ${alias}?`, danger: true, confirmText: 'Remove alias',
    text: `Code that uses ${alias} gets "bucket not found" from then on; ${name.value} itself doesn't change.`,
  })) return
  try {
    await apiFetch(`/aliases/${alias}`, { method: 'DELETE' })
    await reload()
    notify.success(`${alias} removed`)
  } catch (err) { notify.error(err, "The alias couldn't be removed") }
}

// ── transfer / delete ──────────────────────────────────────────────────────
const transferOpen = ref(false)

async function remove() {
  const b = bucket.value!
  if (!await confirmDialog.confirm({
    title: `Delete ${b.name}?`, danger: true, confirmText: 'Delete', typeToConfirm: b.name,
    text: 'It disappears for everyone at once. You can restore it for 30 days.',
    consequences: [
      'Functions and choreographies that use it (or its aliases) get "bucket not found".',
      'Choreography triggers on it stop.',
      'Its files are kept for 30 days and are not billed meanwhile; then they are removed for good.',
    ],
  })) return
  try {
    await call('', { method: 'DELETE' })
    notify.success(`${b.name} deleted — you can restore it for 30 days`, { to: '/buckets?show=deleted' })
    await navigateTo('/buckets')
  } catch (err) { notify.error(err, `${b.name} couldn't be deleted`) }
}
</script>
