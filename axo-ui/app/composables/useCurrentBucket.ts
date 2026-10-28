import type { Bucket, BucketRole } from '../utils/buckets'

type FetchOpts = Parameters<ReturnType<typeof useApi>['apiFetch']>[1]

// The bucket of the page (/buckets/{owner}/{name}/...): its card, the
// person's role on it, and calls about it (/buckets/{owner}/{name}...).
// The frame page loads it; every tab reads the same state.
export function useCurrentBucket() {
  const route = useRoute()
  const { apiFetch } = useApi()

  const name = computed(() => `${route.params.owner ?? ''}/${route.params.name ?? ''}`)
  const bucket = useState<Bucket | null>(`bucket:${name.value}`, () => null)
  const role = computed(() => bucket.value?.my_role ?? null)
  const can = (needed: BucketRole) => bucketRoleAtLeast(role.value, needed)

  /** A call about this bucket, e.g. call('/access') = /buckets/{owner}/{name}/access. */
  function call<T = any>(path = '', opts: FetchOpts = {}): Promise<T> {
    return apiFetch<T>(`/buckets/${name.value}${path}`, opts)
  }

  async function reload(): Promise<Bucket> {
    const fresh = await call<Bucket>()
    // The card from the list carries the figures; keep them across reloads.
    bucket.value = { ...(bucket.value ?? {}), ...fresh } as Bucket
    return bucket.value
  }

  return { name, bucket, role, can, call, reload }
}
