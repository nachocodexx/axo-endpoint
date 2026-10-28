// VE roles, lowest first (the Control Plane's permission sets are cumulative).
export const VE_ROLES = ['viewer', 'developer', 'admin', 'owner'] as const
export type VeRole = typeof VE_ROLES[number]

/** Does `role` include everything `needed` may do? */
export function roleAtLeast(role: string | null | undefined, needed: VeRole): boolean {
  if (!role) return false
  return VE_ROLES.indexOf(role as VeRole) >= VE_ROLES.indexOf(needed)
}

export const ROLE_INFO: Record<VeRole, { title: string; text: string }> = {
  viewer: { title: 'Viewer', text: 'Sees everything in the VE: functions, jobs, figures. Changes nothing.' },
  developer: { title: 'Developer', text: 'Also registers functions, runs jobs, objects and choreographies.' },
  admin: { title: 'Admin', text: 'Also runs the VE: settings, endpoints, policies, who has access, costs.' },
  owner: { title: 'Owner', text: 'Also pays for it: plan, budget, cancelling and deleting the VE.' },
}

export const roleTitle = (role?: string | null) => (role && ROLE_INFO[role as VeRole]?.title) || '—'
