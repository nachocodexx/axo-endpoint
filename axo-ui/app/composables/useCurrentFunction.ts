/** The function of the page (/ves/{id}/functions/{functionId}/...): its
 *  versions (newest first), the newest live one, and whether this person may
 *  delete a version (Admins any; a Developer only versions they registered). */
export function useCurrentFunction() {
  const route = useRoute()
  const { can } = useCurrentVe()
  const functions = useFunctions()
  const me = useMeStore()
  const functionId = computed(() => String(route.params.functionId ?? ''))
  const row = computed(() => functions.byId(functionId.value))
  const latest = computed(() => row.value?.latest)
  const versions = computed(() => row.value?.versions ?? [])
  const mayDelete = (v: { registered_by?: string | null }) =>
    can('admin') || (can('developer') && !!v.registered_by && v.registered_by === me.me?.user_id)
  return { functionId, row, latest, versions, mayDelete, functions }
}
