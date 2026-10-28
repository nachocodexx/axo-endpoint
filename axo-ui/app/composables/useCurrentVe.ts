import type { VeCard } from 'axo-ui-kit/app/types/cp'

const VEM_UP = ['Running', 'Scaling']

type FetchOpts = Parameters<ReturnType<typeof useApi>['apiFetch']>[1]

// The VE of the page (the `id` in /ves/{id}/...): its card from the VEs
// store, the person's role in it, and calls to its VEM through the Control
// Plane's front door (/ves/{id}/api/...), which checks that role again.
export function useCurrentVe() {
  const route = useRoute()
  const store = useVesStore()
  const { apiFetch } = useApi()

  const id = computed(() => String(route.params.id ?? ''))
  const ve = computed<VeCard | undefined>(() => store.byId(id.value))
  const role = computed(() => ve.value?.my_role ?? null)
  const can = (needed: VeRole) => roleAtLeast(role.value, needed)
  /** Whether its VEM answers now. While it's creating / starting / waking
   *  the front door says 503, and a call to a sleeping VE wakes it -- so
   *  pages that only show things don't call then. */
  const vemUp = computed(() => VEM_UP.includes(ve.value?.lifecycle ?? ''))

  /** A VEM call, e.g. api('/ve/settings'). */
  function api<T = any>(path: string, opts: FetchOpts = {}): Promise<T> {
    return apiFetch<T>(`/ves/${id.value}/api${path}`, opts)
  }

  /** A Control Plane call about this VE, e.g. cp('/costs') = /ves/{id}/costs. */
  function cp<T = any>(path = '', opts: FetchOpts = {}): Promise<T> {
    return apiFetch<T>(`/ves/${id.value}${path}`, opts)
  }

  async function reload(): Promise<VeCard> {
    const card = await cp<VeCard>()
    store.put(card)
    return card
  }

  return { id, ve, role, can, vemUp, api, cp, reload }
}
