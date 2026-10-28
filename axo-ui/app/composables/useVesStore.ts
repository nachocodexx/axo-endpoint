import { defineStore } from 'pinia'
import type { VeCard } from 'axo-ui-kit/app/types/cp'

// The VEs the person can see (GET /ves), kept current by the live channel
// (the default layout feeds `put` / `remove` from useLive().onVe). Used by
// the VE list, the VE switcher in the top bar and every VE page.
export const useVesStore = defineStore('ves', () => {
  const list = ref<VeCard[]>([])
  const loaded = ref(false)
  const error = ref<unknown>(null)
  let loading: Promise<void> | null = null

  function load(): Promise<void> {
    loading ??= useApi().apiFetch<VeCard[]>('/ves')
      .then((ves) => { list.value = ves; error.value = null })
      .catch((err) => { error.value = err })
      .finally(() => { loaded.value = true; loading = null })
    return loading
  }

  const byId = (id: string | null | undefined) => list.value.find(v => v.ve_id === id)

  /** A newer card (keeps fields the socket's card leaves out, e.g. my_role). */
  function put(card: VeCard) {
    const i = list.value.findIndex(v => v.ve_id === card.ve_id)
    if (i >= 0) list.value[i] = { ...list.value[i], ...card, my_role: card.my_role ?? list.value[i]!.my_role }
    else list.value.push(card)
  }

  function remove(id: string) {
    list.value = list.value.filter(v => v.ve_id !== id)
  }

  return { list, loaded, error, load, byId, put, remove }
})
