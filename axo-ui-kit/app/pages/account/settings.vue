<template>
  <div class="axo-settings">
    <AxoPageHeader title="Settings" icon="mdi-cog-outline" :crumbs="[{ title: 'Account' }, { title: 'Settings' }]"
                   subtitle="How the platform shows things to you. Changes apply at once and are saved to your account." />

    <AxoErrorAlert v-if="!state" title="Your settings couldn't load" :error="loadError" :retry="reload" class="mb-4" />

    <v-row v-else>
      <v-col cols="12" md="7" lg="8" class="d-flex flex-column ga-4">
        <AxoPanel v-for="group in GROUPS" :key="group.id" :title="group.title" :icon="group.icon" stacked>
          <div v-for="field in fieldsOf(group.id)" :key="field.name" class="axo-setting">
            <div class="axo-setting__text">
              <div class="text-body-2 font-weight-medium">{{ field.title }}</div>
              <div v-if="field.help" class="text-caption axo-muted">{{ field.help }}</div>
              <div class="text-caption mt-1">
                <span v-if="isOwn(field.name)" class="axo-muted">
                  Your choice ·
                  <a href="#" class="axo-link" @click.prevent="change(field.name, null)">use the default ({{ labelOf(field, state.defaults[field.name]) }})</a>
                </span>
                <span v-else class="axo-muted">Platform default</span>
              </div>
            </div>
            <div class="axo-setting__control">
              <template v-if="field.name === 'time_zone'">
                <v-autocomplete :model-value="settings.time_zone" :items="zones" density="compact" variant="outlined"
                                hide-details auto-select-first :loading="!zones.length" aria-label="Time zone"
                                @update:model-value="(v: string | null) => v && change('time_zone', v)" />
                <a v-if="browserZone && browserZone !== settings.time_zone && zones.includes(browserZone)" href="#"
                   class="axo-link text-caption d-inline-block mt-1" @click.prevent="change('time_zone', browserZone)">
                  Use this computer's ({{ browserZone }})
                </a>
              </template>
              <v-btn-toggle v-else-if="(field.choices?.length ?? 0) <= 3" :model-value="settings[field.name]" mandatory divided
                            density="comfortable" variant="outlined" color="primary" rounded="lg"
                            @update:model-value="(v: string) => change(field.name, v)">
                <v-btn v-for="c in field.choices" :key="c" :value="c" :prepend-icon="ICONS[c]" class="text-none">
                  {{ labelOf(field, c) }}
                </v-btn>
              </v-btn-toggle>
              <v-select v-else :model-value="settings[field.name]" :items="(field.choices ?? []).map(c => ({ value: c, title: labelOf(field, c) }))"
                        density="compact" variant="outlined" hide-details :aria-label="field.title"
                        @update:model-value="(v: string) => change(field.name, v)" />
            </div>
          </div>
        </AxoPanel>
      </v-col>

      <v-col cols="12" md="5" lg="4">
        <div class="axo-settings__preview">
          <AxoPanel title="How things look" icon="mdi-eye-outline" stacked>
            <AxoKeyValue :items="preview" :columns="1" />
            <div class="text-caption axo-muted mt-3">
              Dates and times are kept in UTC and shown in your time zone. Bills count months in UTC for everyone,
              so a bill never moves when someone changes their time zone.
            </div>
          </AxoPanel>
        </div>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import type { Settings } from '../../utils/format'
import type { SettingField } from '../../composables/useSettings'

// The person's settings (GET / PATCH /me/settings): region (time zone,
// formats), appearance (theme, text size, table spacing) and the periods
// pages open with. A field they never set follows the platform default.
useHead({ title: 'Settings · axo' })

const { state, settings, load, update } = useSettings()
const notify = useNotifier()
const loadError = ref<unknown>(null)
const zones = ref<string[]>([])
const browserZone = ref('')

const GROUPS = [
  { id: 'region', title: 'Region', icon: 'mdi-earth' },
  { id: 'appearance', title: 'Appearance', icon: 'mdi-palette-outline' },
  { id: 'defaults', title: 'Pages open with', icon: 'mdi-tune-variant' },
] as const

const ICONS: Record<string, string> = {
  light: 'mdi-white-balance-sunny', dark: 'mdi-weather-night', system: 'mdi-laptop',
}
const RANGES: Record<string, string> = { '1h': 'Last hour', '24h': 'Last 24 hours', '7d': 'Last 7 days', '30d': 'Last 30 days' }
const WORDS: Record<string, string> = {
  en: 'English', sunday: 'Sunday', monday: 'Monday', light: 'Light', dark: 'Dark', system: 'System',
  small: 'Small', normal: 'Normal', large: 'Large', comfortable: 'Comfortable', compact: 'Compact',
}

// A fixed moment for the examples: Oct 19, 2026, 14:05 UTC.
const SAMPLE = Date.UTC(2026, 9, 19, 14, 5) / 1000

function labelOf(field: SettingField, value: string | undefined): string {
  if (value == null) return '—'
  switch (field.name) {
    case 'date_format': return `${withSettings({ date_format: value as Settings['date_format'] }, () => formatDate(SAMPLE))}`
    case 'clock': return value === '24h' ? '24-hour' : '12-hour'
    case 'number_format': return withSettings({ number_format: value as Settings['number_format'] }, () => formatNumber(1234567.89, false))
    case 'chart_range':
    case 'activity_window': return RANGES[value] ?? value
    case 'time_zone': return value
    default: return WORDS[value] ?? value
  }
}

const fieldsOf = (group: string) => (state.value?.fields ?? []).filter(f => f.group === group)
const isOwn = (name: keyof Settings) => state.value?.own[name] != null

// The first render shows the sample on the server and in the browser alike
// (no hydration mismatch); the clock starts once mounted.
const now = ref(SAMPLE)
const preview = computed(() => [
  { label: 'Now', value: formatDateTime(now.value) },
  { label: 'To the second', value: formatExactTime(now.value) },
  { label: 'Time zone', value: `${settings.value.time_zone} (${timeZoneLabel(now.value)})` },
  { label: 'Money', value: formatMoney(123456) },
  { label: 'A number', value: formatNumber(1234567.89, false) },
  { label: 'A size', value: formatBytes(1.5 * 1024 ** 3) },
])

async function change(name: keyof Settings, value: string | null) {
  try {
    await update({ [name]: value })
  } catch (err) {
    notify.error(err, "That setting couldn't be saved")
  }
}

async function reload() {
  loadError.value = null
  await load(true)
  if (!state.value) loadError.value = new Error('The Control Plane did not answer')
}

onMounted(async () => {
  browserZone.value = Intl.DateTimeFormat().resolvedOptions().timeZone
  if (!state.value) await reload()
  try {
    zones.value = (await useApi().apiFetch<{ time_zones: string[] }>('/settings/time-zones')).time_zones
  } catch { zones.value = [settings.value.time_zone] }
})
usePolling(() => { now.value = Date.now() / 1000 }, 1000)
</script>

<style scoped>
.axo-settings { max-width: 1100px; }
.axo-setting { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; padding: 12px 0; }
.axo-setting + .axo-setting { border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity)); }
.axo-setting__text { flex: 1 1 240px; min-width: 0; }
.axo-setting__control { flex: 0 1 320px; min-width: 220px; }
.axo-settings__preview { position: sticky; top: 80px; }
</style>
