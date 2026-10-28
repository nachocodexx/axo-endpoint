import type { ChoreographyRow, DesignerInfo, Suggestions } from '../utils/choreographies'

/** The VE's choreographies (GET /choreographies, with each one's run
 *  figures), the designer's description of every setting (read once per VE)
 *  and the "used before" suggestions. Shared by the choreography pages. */
export function useChoreographies() {
  const { id, api, vemUp } = useCurrentVe()
  const state = useState<{ ve: string | null; rows: ChoreographyRow[]; loadedAt: number }>(
    'axo-choreographies', () => ({ ve: null, rows: [], loadedAt: 0 }))
  const designerState = useState<{ ve: string | null; info: DesignerInfo | null }>('axo-ch-designer', () => ({ ve: null, info: null }))
  const loading = ref(false)
  const error = ref<unknown>(null)

  async function load(force = false) {
    if (!vemUp.value) return
    if (!force && state.value.ve === id.value && Date.now() - state.value.loadedAt < 5000) return
    loading.value = true
    error.value = null
    try {
      const rows = await api<ChoreographyRow[]>('/choreographies')
      state.value = { ve: id.value, rows, loadedAt: Date.now() }
    } catch (err) {
      error.value = err
    } finally {
      loading.value = false
    }
  }

  async function designer(): Promise<DesignerInfo | null> {
    if (designerState.value.ve === id.value && designerState.value.info) return designerState.value.info
    if (!vemUp.value) return null
    const info = await api<DesignerInfo>('/choreographies/designer')
    designerState.value = { ve: id.value, info }
    return info
  }

  async function suggestions(): Promise<Suggestions> {
    try { return await api<Suggestions>('/choreography-suggestions') } catch { return { event_types: [], conditions: [] } }
  }

  const rows = computed(() => (state.value.ve === id.value ? state.value.rows : []))
  const byName = (name: string) => rows.value.find(r => r.name === name)
  const byId = (choreographyId: string) => rows.value.find(r => r.choreography_id === choreographyId)
  const info = computed(() => (designerState.value.ve === id.value ? designerState.value.info : null))
  return { rows, loading, error, load, byName, byId, designer, info, suggestions }
}
