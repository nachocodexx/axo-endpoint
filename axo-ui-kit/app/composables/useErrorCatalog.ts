import type { CatalogEntry, ErrorCatalog } from '../types/cp'

let loading: Promise<ErrorCatalog | null> | null = null

// GET /errors, loaded once and kept for the whole visit.
export function useErrorCatalog() {
  const catalog = useState<ErrorCatalog | null>('axo-error-catalog', () => null)
  const { apiFetch } = useApi()

  async function load(): Promise<ErrorCatalog | null> {
    if (catalog.value) return catalog.value
    loading ??= apiFetch<ErrorCatalog>('/errors').then((c) => { catalog.value = c; return c })
      .catch(() => null).finally(() => { loading = null })
    return loading
  }

  function lookup(name?: string | null): CatalogEntry | undefined {
    return name ? catalog.value?.errors.find(e => e.name === name) : undefined
  }

  return { catalog, load, lookup }
}
