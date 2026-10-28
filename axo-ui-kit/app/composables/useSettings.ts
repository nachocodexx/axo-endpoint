import { DEFAULT_SETTINGS, type Settings } from '../utils/format'

export interface SettingField {
  name: keyof Settings
  group: 'region' | 'appearance' | 'defaults'
  title: string
  help: string
  default: string
  choices?: string[]
}

/** GET /me/settings: what applies, what the person chose, the platform defaults. */
export interface SettingsAnswer {
  settings: Settings
  own: Partial<Settings>
  defaults: Settings
  fields: SettingField[]
}

// The signed-in person's settings, loaded before the first page renders
// (middleware/auth.global.ts, also on the UI's server) and shared through
// one useState, which the formatters in utils/format.ts read
// (plugins/settings.ts provides it). Changes apply at once and are saved.
export function useSettings() {
  const state = useState<SettingsAnswer | null>('axo-settings', () => null)
  const settings = computed<Settings>(() => state.value?.settings ?? DEFAULT_SETTINGS)
  const signedIn = computed(() => state.value != null)

  async function load(force = false) {
    if (state.value && !force) return
    if (!useAuthSession().value) return
    try {
      state.value = await useApi().apiFetch<SettingsAnswer>('/me/settings', { quiet401: true })
    } catch { /* the defaults until the next page */ }
  }

  /** Only the fields given; `null` = follow the platform default again. */
  async function update(changes: Partial<Record<keyof Settings, string | null>>) {
    const before = state.value
    if (before) {
      const applied = Object.fromEntries(Object.entries(changes).map(([k, v]) => [k, v ?? before.defaults[k as keyof Settings]]))
      state.value = { ...before, settings: { ...before.settings, ...applied } as Settings }
    }
    try {
      state.value = await useApi().apiFetch<SettingsAnswer>('/me/settings', { method: 'PATCH', body: changes })
    } catch (err) {
      state.value = before
      throw err
    }
  }

  function clear() { state.value = null }

  return { state, settings, signedIn, load, update, clear }
}
