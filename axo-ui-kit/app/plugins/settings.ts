import { DEFAULT_SETTINGS } from '../utils/format'

// Hands the person's settings to the formatters (utils/format.ts reads
// `$axoSettings`, on the server and in the browser alike) and applies the
// text size to the whole page.
export default defineNuxtPlugin(() => {
  const state = useState<SettingsAnswer | null>('axo-settings', () => null)
  useHead(() => ({ htmlAttrs: { class: `axo-text-${(state.value?.settings ?? DEFAULT_SETTINGS).font_size}` } }))
  return { provide: { axoSettings: state } }
})
