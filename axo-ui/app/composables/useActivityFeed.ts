import type { ActivityRow } from '../utils/activity'

/** A VEM history list (`/history`, `/endpoints/{id}/history`, ...) a page at
 *  a time: `load()` the newest page, `loadMore()` the page after the last
 *  row shown ("Load more"), `refresh()` adds what's new on top without
 *  dropping the pages already loaded. `query()` (areas, since_minutes, ...)
 *  is sent with every page. A page shorter than `pageSize` is the last. */
export function useActivityFeed(
  path: () => string | null,
  query: () => Record<string, string | number | undefined> = () => ({}),
  pageSize = 50,
) {
  const { api } = useCurrentVe()
  const rows = ref<ActivityRow[]>([])
  const more = ref(false)
  const loading = ref(false)
  const loadingMore = ref(false)
  const error = ref<unknown>(null)
  let generation = 0          // a newer load() wins over an older one still answering

  function url(extra: Record<string, string | number | undefined> = {}): string | null {
    const base = path()
    if (!base) return null
    const params = new URLSearchParams()
    for (const [k, v] of Object.entries({ ...query(), limit: pageSize, ...extra })) {
      if (v !== undefined && String(v) !== '') params.set(k, String(v))
    }
    return `${base}?${params}`
  }

  async function load() {
    const target = url()
    if (!target) return
    const mine = ++generation
    loading.value = true
    error.value = null
    try {
      const page = await api<ActivityRow[]>(target)
      if (mine !== generation) return
      rows.value = page
      more.value = page.length === pageSize
    } catch (err) {
      if (mine === generation) error.value = err
    } finally {
      if (mine === generation) loading.value = false
    }
  }

  async function loadMore() {
    const last = rows.value.at(-1)
    const target = last && url({ before: last.created_at, before_id: last.event_id })
    if (!target || loadingMore.value) return
    const mine = generation
    loadingMore.value = true
    try {
      const page = await api<ActivityRow[]>(target)
      if (mine !== generation) return
      rows.value = [...rows.value, ...page]
      more.value = page.length === pageSize
    } catch (err) {
      useNotifier().error(err, "Older activity couldn't load")
    } finally {
      loadingMore.value = false
    }
  }

  async function refresh() {
    if (rows.value.length <= pageSize) return load()
    const target = url()
    if (!target) return
    const mine = generation
    try {
      const page = await api<ActivityRow[]>(target)
      if (mine !== generation) return
      const known = new Set(rows.value.map(r => r.event_id))
      const fresh = page.filter(r => !known.has(r.event_id))
      // A whole page of new rows: there may be a gap below them, start over.
      if (fresh.length === page.length) return load()
      rows.value = [...fresh, ...rows.value]
    } catch { /* the next refresh tries again */ }
  }

  return { rows, more, loading, loadingMore, error, load, loadMore, refresh }
}
