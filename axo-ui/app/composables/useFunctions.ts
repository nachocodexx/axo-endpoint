import type { FunctionVersion } from '../utils/functions'

/** The VE's functions (every version, GET /functions), shared by the pages
 *  of one VE and read again when asked (or when another VE is opened). */
export function useFunctions() {
  const { id, api, vemUp } = useCurrentVe()
  const state = useState<{ ve: string | null; all: FunctionVersion[]; loadedAt: number }>(
    'axo-functions', () => ({ ve: null, all: [], loadedAt: 0 }))
  const loading = ref(false)
  const error = ref<unknown>(null)

  async function load(force = false) {
    if (!vemUp.value) return
    if (!force && state.value.ve === id.value && Date.now() - state.value.loadedAt < 5000) return
    loading.value = true
    error.value = null
    try {
      const all = await api<FunctionVersion[]>('/functions')
      state.value = { ve: id.value, all, loadedAt: Date.now() }
    } catch (err) {
      error.value = err
    } finally {
      loading.value = false
    }
  }

  const all = computed(() => (state.value.ve === id.value ? state.value.all : []))
  // Classes are registered as functions too (call_style "object"): they're on the Objects pages.
  const rows = computed(() => groupFunctions(all.value.filter(v => v.runtime_spec?.call_style !== 'object')))
  const byId = (fid: string) => rows.value.find(r => r.function_id === fid)
  const nameOf = (fid: string) => byId(fid)?.name ?? fid

  return { all, rows, loading, error, load, byId, nameOf }
}
