import { AxoApiError } from '../utils/apiError'

type FetchOpts = Parameters<typeof $fetch>[1] & { /** don't redirect to /login on 401 */ quiet401?: boolean }

let refreshing: Promise<boolean> | null = null

// The Control Plane client: adds the session headers, turns every failure
// into an AxoApiError (catalog name + number), and on an expired session
// tries one refresh before sending the person to /login.
export function useApi() {
  const config = useRuntimeConfig()
  const session = useAuthSession()
  // While rendering on the server, the CP may sit at another address.
  const baseURL = ((import.meta.server && config.apiBaseServer) || config.public.apiBase) as string

  function headers(extra?: Record<string, string>): Record<string, string> {
    const out: Record<string, string> = { ...(extra || {}) }
    if (session.value) {
      out.Authorization = `Bearer ${session.value.access_token}`
      out['Temporal-Secret-Key'] = session.value.temporal_secret
    }
    return out
  }

  async function refresh(): Promise<boolean> {
    if (!session.value) return false
    refreshing ??= $fetch<{ access_token: string; temporal_secret: string }>('/auth/refresh', {
      baseURL, method: 'POST', headers: headers(),
    }).then((fresh) => {
      session.value = { ...session.value!, ...fresh }
      return true
    }).catch(() => false).finally(() => { refreshing = null })
    return refreshing
  }

  async function apiFetch<T = any>(path: string, opts: FetchOpts = {}, retried = false): Promise<T> {
    const { quiet401, ...rest } = opts
    try {
      return await $fetch<T>(path, {
        baseURL,
        ...rest,
        headers: headers(rest.headers as Record<string, string>),
      } as any)
    } catch (raw: any) {
      const err = AxoApiError.from(raw)
      if (err.errorName === 'PASSWORD_CHANGE_REQUIRED' && import.meta.client
        && useRoute().path !== '/account/password') {
        await navigateTo('/account/password')
      }
      if (err.status === 401 && !path.startsWith('/auth/')) {
        if (!retried && await refresh()) return apiFetch<T>(path, opts, true)
        session.value = null
        if (!quiet401 && import.meta.client) {
          await navigateTo({ path: '/login', query: { next: useRoute().fullPath } })
        }
      }
      throw err
    }
  }

  /** ws(s):// URL of a Control Plane socket, with the session as query parameters. */
  function wsUrl(path: string): string {
    const base = (config.public.apiBase as string).replace(/^http/, 'ws')
    const q = session.value
      ? `${path.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(session.value.access_token)}`
        + `&temporal_secret=${encodeURIComponent(session.value.temporal_secret)}`
      : ''
    return `${base}${path}${q}`
  }

  return { apiFetch, wsUrl, session }
}
