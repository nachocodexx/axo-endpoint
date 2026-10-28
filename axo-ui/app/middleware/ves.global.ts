// The person's VEs, loaded before a VE page renders (also on the server),
// so the first render already has the VE's name, role and status: the
// layout, the VE switcher and the page then match what the browser shows.
export default defineNuxtRouteMiddleware(async (to) => {
  if (!to.path.startsWith('/ves') || !useAuthSession().value) return
  const ves = useVesStore()
  if (!ves.loaded) await ves.load()
})
