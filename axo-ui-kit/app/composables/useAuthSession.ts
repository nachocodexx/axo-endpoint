import type { Session } from '../types/cp'

// One cookie with the session (access token + temporal secret, the pair the
// Control Plane checks on every call), so SSR and the browser agree on
// "signed in" from the first render. Every caller shares one state that
// writes through to the cookie: separate useCookie refs only sync later, so
// a call right after signing in could still see "signed out".
export function useAuthSession() {
  const cookie = useCookie<Session | null>('axo_session', { default: () => null, sameSite: 'lax' })
  const state = useState<Session | null>('axo_session', () => cookie.value)
  return computed<Session | null>({
    get: () => state.value,
    set: (value) => {
      state.value = value
      cookie.value = value
    },
  })
}
