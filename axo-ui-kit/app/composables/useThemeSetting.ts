export type ThemeName = 'axoLight' | 'axoDark'

// The theme comes from the person's settings (light / dark / follow the
// computer). The theme last shown is also kept in a cookie, so the UI's
// server renders the right one at once (no light-then-dark flash): for
// "system" (which only the browser knows) and on signed-out pages.
export function useThemeSetting() {
  const theme = useTheme()
  const { settings, signedIn, update } = useSettings()
  const shown = useCookie<ThemeName>('axo_theme', { default: () => 'axoLight', sameSite: 'lax', maxAge: 365 * 86400 })
  const systemDark = useState<boolean>('axo-system-dark', () => shown.value === 'axoDark')

  if (import.meta.client) {
    onMounted(() => {
      const query = window.matchMedia('(prefers-color-scheme: dark)')
      systemDark.value = query.matches
      const follow = (e: MediaQueryListEvent) => { systemDark.value = e.matches }
      query.addEventListener('change', follow)
      onBeforeUnmount(() => query.removeEventListener('change', follow))
    })
  }

  const preference = computed(() => (signedIn.value ? settings.value.theme : (shown.value === 'axoDark' ? 'dark' : 'light')))
  const resolved = computed<ThemeName>(() =>
    preference.value === 'dark' || (preference.value === 'system' && systemDark.value) ? 'axoDark' : 'axoLight')
  watch(resolved, (name) => { theme.change(name); shown.value = name }, { immediate: true })

  /** The bar's sun / moon button: picks light or dark (and saves it when signed in). */
  function toggle() {
    const next = resolved.value === 'axoDark' ? 'light' : 'dark'
    if (signedIn.value) update({ theme: next }).catch(err => useNotifier().error(err, "The theme couldn't be saved"))
    else shown.value = next === 'dark' ? 'axoDark' : 'axoLight'
  }

  const isDark = computed(() => resolved.value === 'axoDark')
  return { preference, resolved, toggle, isDark }
}
