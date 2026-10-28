<template>
  <div>
    <AxoPageHeader title="API keys" icon="mdi-key-outline"
                   subtitle="Keys your programs use to act for you: the axo library, scripts and the API reference use one instead of your own sign-in."
                   help="A key acts for you, with at most the role you give it (and never more than your own role in a VE). Cancel one at any time: your own password doesn't change. A key is shown once, when it's made.">
      <template #actions>
        <AxoBtn prepend-icon="mdi-plus" :disabled="isGuest" @click="openNew">New key</AxoBtn>
      </template>
    </AxoPageHeader>

    <v-alert v-if="isGuest" type="info" variant="tonal" density="compact" class="mb-4">
      Guest accounts can't make keys. Sign up for a full account to use the axo library.
    </v-alert>
    <AxoErrorAlert v-if="error" :error="error" title="Your keys couldn't load" :retry="load" class="mb-4" />

    <AxoDataTable :headers="headers" :items="keys" item-value="key_id" :loading="loading" :searchable="keys.length > 8"
                  empty-icon="mdi-key-outline" empty-title="No keys yet"
                  empty-text="Make one with New key, then put it in AXO_API_KEY for the axo library.">
      <template #item.name="{ item }">
        <div class="font-weight-medium">{{ item.name }}</div>
        <div class="d-flex flex-wrap ga-1 mt-1">
          <v-chip v-if="state(item) === 'expired'" size="x-small" color="error" variant="tonal">Expired</v-chip>
          <v-chip v-else-if="state(item) === 'expiring'" size="x-small" color="warning" variant="tonal">Expires soon</v-chip>
          <v-chip v-if="unused(item)" size="x-small" variant="tonal" :title="'Not used for over 90 days: cancel it if nothing needs it'">
            Not used for a long time
          </v-chip>
        </div>
      </template>
      <template #item.ve_id="{ item }">
        <span v-if="!item.ve_id" class="text-body-2">All my VEs</span>
        <NuxtLink v-else-if="ves.byId(item.ve_id)" :to="`/ves/${item.ve_id}`" class="axo-link text-body-2">{{ ves.byId(item.ve_id)!.name }}</NuxtLink>
        <span v-else class="text-body-2 axo-muted">A VE you no longer see</span>
      </template>
      <template #item.max_role="{ item }"><span class="text-body-2">{{ roleTitle(item.max_role) }}</span></template>
      <template #item.created_at="{ item }">
        <span class="text-body-2 text-no-wrap" :title="formatExactTime(item.created_at)">{{ formatDate(item.created_at) }}</span>
      </template>
      <template #item.last_used_at="{ item }">
        <span class="text-body-2 text-no-wrap" :title="item.last_used_at ? formatExactTime(item.last_used_at) : ''">
          {{ item.last_used_at ? timeAgo(item.last_used_at) : 'Never' }}</span>
      </template>
      <template #item.expires_at="{ item }">
        <span class="text-body-2 text-no-wrap" :class="{ 'text-error': state(item) === 'expired' }"
              :title="item.expires_at ? formatExactTime(item.expires_at) : ''">
          {{ item.expires_at ? formatDate(item.expires_at) : 'Never' }}</span>
      </template>
      <template #item.actions="{ item }">
        <v-btn size="small" variant="text" color="error" class="text-none" :loading="busy === item.key_id" @click.stop="revoke(item)">
          Cancel key
        </v-btn>
      </template>
    </AxoDataTable>

    <!-- New key -->
    <v-dialog v-model="newOpen" max-width="560" :persistent="!!made">
      <v-card v-if="!made">
        <v-card-title class="pt-5 px-6">New API key</v-card-title>
        <v-card-text class="px-6">
          <v-form ref="form" @submit.prevent="create">
            <v-text-field v-model="name" label="Name" placeholder="e.g. laptop-ana" :rules="[nameRule]" counter="80" autofocus
                          hint="So you know later where it's used." persistent-hint class="mb-3" />
            <v-select v-model="veId" :items="veChoices" label="VE" hint="All my VEs: the key works on every VE you have a role in, now and later."
                      persistent-hint class="mb-3" />
            <v-select v-model="role" :items="roleChoices" label="Highest role" item-title="title" item-value="value"
                      :hint="roleHint" persistent-hint class="mb-3">
              <template #item="{ props: p, item }">
                <v-list-item v-bind="p" :subtitle="ROLE_INFO[item.value as VeRole].text" :disabled="item.disabled" />
              </template>
            </v-select>
            <v-select v-model="expires" :items="EXPIRY" label="Expires" item-title="title" item-value="days"
                      :hint="expires ? `On ${formatDate(Date.now() / 1000 + expires * 86400)}` : 'It works until you cancel it.'" persistent-hint />
          </v-form>
        </v-card-text>
        <v-card-actions class="px-6 pb-5">
          <v-spacer />
          <v-btn variant="text" class="text-none" @click="newOpen = false">Cancel</v-btn>
          <AxoBtn :loading="creating" @click="create">Make the key</AxoBtn>
        </v-card-actions>
      </v-card>

      <!-- Shown once -->
      <v-card v-else>
        <v-card-title class="pt-5 px-6 d-flex align-center ga-2">
          <v-icon color="success">mdi-check-circle</v-icon> Your new key
        </v-card-title>
        <v-card-text class="px-6">
          <v-alert type="warning" variant="tonal" density="compact" class="mb-4">
            Copy it now: it's shown <b>only this once</b>. If you lose it, cancel it and make a new one.
          </v-alert>
          <div class="axo-key d-flex align-center ga-2 pa-3 mb-4">
            <span class="axo-mono axo-selectable flex-grow-1 min-w-0" style="word-break: break-all">{{ made.key }}</span>
            <v-btn size="small" variant="tonal" color="primary" class="text-none flex-shrink-0" :prepend-icon="copied === 'key' ? 'mdi-check' : 'mdi-content-copy'"
                   @click="copy(made.key ?? '', 'key')">{{ copied === 'key' ? 'Copied' : 'Copy' }}</v-btn>
          </div>
          <div class="d-flex align-center ga-2 mb-1">
            <span class="text-subtitle-2">Ready to paste in a terminal</span>
            <v-spacer />
            <v-btn size="small" variant="tonal" color="primary" class="text-none" :prepend-icon="copied === 'env' ? 'mdi-check' : 'mdi-content-copy'"
                   @click="copy(snippet, 'env')">{{ copied === 'env' ? 'Copied' : 'Copy' }}</v-btn>
          </div>
          <div class="text-body-2 axo-muted mb-2">The three settings the axo library reads (key, VE, address).</div>
          <v-select v-if="!made.ve_id && exampleChoices.length > 1" v-model="exampleVe" :items="exampleChoices" label="VE for the example"
                    density="compact" hide-details class="mb-2" />
          <pre class="axo-snippet axo-mono axo-selectable">{{ snippet }}</pre>
          <div class="text-body-2 axo-muted mt-2">Then in Python: <span class="axo-mono">AxoClient()</span> finds them by itself.</div>
          <v-checkbox v-model="saved" label="I saved the key somewhere safe" hide-details density="compact" class="mt-3" />
        </v-card-text>
        <v-card-actions class="px-6 pb-5">
          <v-spacer />
          <AxoBtn :disabled="!saved" @click="newOpen = false">Done</AxoBtn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { ROLE_INFO, roleAtLeast, roleTitle, type VeRole } from '../../utils/roles'

// The person's API keys (the Control Plane calls them /sdk-keys): make one
// (shown once, with the AXO_API_KEY / AXO_VE / AXO_API_URL lines), see
// where and when each was used, cancel one (type its name).
interface SdkKey {
  key_id: string
  name: string
  ve_id: string | null
  max_role: VeRole
  created_at: number
  expires_at: number | null
  last_used_at: number | null
  key?: string
}

useHead({ title: 'API keys · axo' })
const { apiFetch } = useApi()
const me = useMeStore()
const ves = useVesStore()
const notify = useNotifier()
const confirmDialog = useConfirm()
const apiBase = useRuntimeConfig().public.apiBase as string

const isGuest = computed(() => me.me?.platform_role === 'guest')
const keys = ref<SdkKey[]>([])
const loading = ref(true)
const error = ref<unknown>(null)
async function load() {
  error.value = null
  try { keys.value = await apiFetch<SdkKey[]>('/sdk-keys') } catch (err) { error.value = err } finally { loading.value = false }
}
onMounted(() => { load(); if (!ves.loaded) ves.load() })

const WIDE = { headerProps: { class: 'd-none d-md-table-cell' }, cellProps: { class: 'd-none d-md-table-cell' } }
const headers = [
  { title: 'Name', key: 'name' },
  { title: 'VE', key: 've_id', sortable: false },
  { title: 'Highest role', key: 'max_role', ...WIDE },
  { title: 'Made', key: 'created_at', ...WIDE },
  { title: 'Last used', key: 'last_used_at' },
  { title: 'Expires', key: 'expires_at', ...WIDE },
  { title: '', key: 'actions', sortable: false, align: 'end' as const },
]
const now = () => Date.now() / 1000
const DAY = 86400
function state(k: SdkKey): 'expired' | 'expiring' | 'ok' {
  if (k.expires_at && k.expires_at <= now()) return 'expired'
  if (k.expires_at && k.expires_at - now() < 14 * DAY) return 'expiring'
  return 'ok'
}
const unused = (k: SdkKey) => state(k) !== 'expired' && now() - (k.last_used_at ?? k.created_at) > 90 * DAY

// ── new key ──────────────────────────────────────────────────────────────
const EXPIRY = [
  { title: '30 days', days: 30 },
  { title: '90 days', days: 90 },
  { title: '1 year', days: 365 },
  { title: 'Never', days: null },
]
const newOpen = ref(false)
const form = ref<any>(null)
const name = ref('')
const veId = ref<string | null>(null)
const role = ref<VeRole>('developer')
const expires = ref<number | null>(90)
const creating = ref(false)
const made = ref<SdkKey | null>(null)
const saved = ref(false)
const copied = ref<string | null>(null)
const exampleVe = ref<string | null>(null)

const nameRule = (v: string) => (!!v?.trim() && v.trim().length <= 80) || 'A name, up to 80 characters'
const myVes = computed(() => ves.list.filter(v => v.my_role && v.desired !== 'deleted'))
const veChoices = computed(() => [{ title: 'All my VEs', value: null },
  ...myVes.value.map(v => ({ title: v.name, value: v.ve_id }))])
// One VE: up to the person's role there; every VE: the role is still capped per VE.
const myTop = computed<VeRole>(() => (veId.value ? (ves.byId(veId.value)?.my_role as VeRole) ?? 'viewer' : 'admin'))
const roleChoices = computed(() => (['viewer', 'developer', 'admin'] as VeRole[]).map(r => ({
  title: ROLE_INFO[r].title, value: r, disabled: !roleAtLeast(myTop.value, r),
})))
const roleHint = computed(() => (veId.value
  ? `Up to your own role in this VE (${roleTitle(myTop.value)}).`
  : 'In each VE the key has this role or your own, whichever is lower.'))
watch(veId, () => { if (!roleAtLeast(myTop.value, role.value)) role.value = 'viewer' })

function openNew() {
  name.value = ''
  veId.value = null
  role.value = 'developer'
  expires.value = 90
  made.value = null
  saved.value = false
  newOpen.value = true
}

async function create() {
  if (!(await form.value?.validate())?.valid) return
  creating.value = true
  try {
    made.value = await apiFetch<SdkKey>('/sdk-keys', { method: 'POST', body: {
      name: name.value.trim(), ve_id: veId.value, max_role: role.value, expires_in_days: expires.value,
    } })
    exampleVe.value = made.value.ve_id ?? exampleChoices.value[0]?.value ?? null
    keys.value = [{ ...made.value, key: undefined }, ...keys.value]
  } catch (err) {
    notify.error(err, "The key couldn't be made")
  } finally {
    creating.value = false
  }
}

const exampleChoices = computed(() => myVes.value.map(v => ({ title: v.name, value: v.ve_id })))
const snippet = computed(() => [
  `export AXO_API_KEY="${made.value?.key ?? ''}"`,
  `export AXO_VE="${made.value?.ve_id ?? exampleVe.value ?? '<your VE id>'}"`,
  `export AXO_API_URL="${apiBase}"`,
].join('\n'))

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = what
    setTimeout(() => { if (copied.value === what) copied.value = null }, 1500)
  } catch {
    notify.warning("Couldn't copy to the clipboard: select the text and copy it")
  }
}

// ── cancel ───────────────────────────────────────────────────────────────
const busy = ref<string | null>(null)
async function revoke(k: SdkKey) {
  if (!await confirmDialog.confirm({
    title: `Cancel the key ${k.name}?`,
    consequences: ['It stops working right away: programs using it get "unauthorized".', "It can't be turned back on."],
    confirmText: 'Cancel the key', cancelText: 'Keep it', danger: true, typeToConfirm: k.name,
  })) return
  busy.value = k.key_id
  try {
    await apiFetch(`/sdk-keys/${k.key_id}`, { method: 'DELETE' })
    keys.value = keys.value.filter(x => x.key_id !== k.key_id)
    notify.success(`The key ${k.name} was cancelled`)
  } catch (err) {
    notify.error(err, "The key couldn't be cancelled")
  } finally {
    busy.value = null
  }
}
</script>

<style scoped>
.axo-key { border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.04); }
.axo-snippet { margin: 0; padding: 12px; border-radius: 8px; font-size: 13px; line-height: 1.6;
  background: rgba(var(--v-theme-on-surface), 0.06); white-space: pre-wrap; word-break: break-all; }
</style>
