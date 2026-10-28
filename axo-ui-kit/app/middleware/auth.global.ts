// Pages need a session unless they say `definePageMeta({ auth: false })`
// (sign-in, sign-up, the error catalog). Signed-in people skip /login.
// Their settings load before the first page renders (dates, numbers, theme).
export default defineNuxtRouteMiddleware(async (to) => {
  const session = useAuthSession()
  const publicPage = to.meta.auth === false
  if (!session.value && !publicPage) {
    return navigateTo({ path: '/login', query: to.fullPath !== '/' ? { next: to.fullPath } : {} })
  }
  if (session.value && (to.path === '/login' || to.path === '/signup')) {
    return navigateTo('/')
  }
  if (session.value) await useSettings().load()
})
