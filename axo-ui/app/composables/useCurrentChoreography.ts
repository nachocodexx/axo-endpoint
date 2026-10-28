import type { Choreography, Visibility } from '../utils/choreographies'

// a load already asked for, shared (browser only: on the server this module
// is shared by everyone's requests)
const inFlight = new Map<string, Promise<void>>()

/** The choreography of the page (/ves/{id}/choreographies/{name}/...): its
 *  newest version (GET /choreographies/{name}), its versions list and who
 *  sees its runs. One copy per page tree (useState by name). */
export function useCurrentChoreography() {
  const route = useRoute()
  const { id, api, vemUp, can } = useCurrentVe()
  const name = computed(() => String(route.params.name ?? ''))
  const state = useState<{ key: string; doc: Choreography | null; versions: VersionRow[]; visibility: Visibility | null; missing: boolean }>(
    'axo-ch-current', () => ({ key: '', doc: null, versions: [], visibility: null, missing: false }))
  const loading = ref(false)
  const error = ref<unknown>(null)
  const key = computed(() => `${id.value}/${name.value}`)

  function load(): Promise<void> {
    if (!vemUp.value || !name.value) return Promise.resolve()
    if (import.meta.server) return fetchAll()
    const k = key.value
    const running = inFlight.get(k)
    if (running) return running
    const p = fetchAll().finally(() => inFlight.delete(k))
    inFlight.set(k, p)
    return p
  }

  async function fetchAll() {
    loading.value = true
    error.value = null
    try {
      const enc = encodeURIComponent(name.value)
      const [doc, versions, visibility] = await Promise.all([
        api<Choreography>(`/choreographies/${enc}`),
        api<VersionRow[]>(`/choreographies/${enc}/versions`),
        api<Visibility>(`/visibility/choreography/${enc}`).catch(() => null),
      ])
      state.value = { key: key.value, doc, versions, visibility, missing: false }
    } catch (err: any) {
      if (err?.status === 404) state.value = { key: key.value, doc: null, versions: [], visibility: null, missing: true }
      else error.value = err
    } finally {
      loading.value = false
    }
  }

  /** One version (for Versions, Compare, a run of an older version). */
  async function version(v: number): Promise<Choreography> {
    return api<Choreography>(`/choreographies/${encodeURIComponent(name.value)}`, { query: { version: v } })
  }

  const mine = computed(() => state.value.key === key.value)
  const doc = computed(() => (mine.value ? state.value.doc : null))
  const versions = computed(() => (mine.value ? state.value.versions : []))
  const visibility = computed(() => (mine.value ? state.value.visibility : null))
  const missing = computed(() => mine.value && state.value.missing)
  const canEdit = computed(() => can('developer'))
  return { name, doc, versions, visibility, missing, loading, error, load, version, canEdit }
}

export interface VersionRow { version: number; name: string; updated_at?: string | null; updated_by?: string | null }
