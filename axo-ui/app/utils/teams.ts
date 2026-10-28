import { bucketRoleTitle } from './buckets'
import { roleTitle } from './roles'

// Teams, invitations and transfers as the Control Plane answers them
// (GET /teams, /teams/{id}, /teams/{id}/resources, /invitations, /transfers).
export const TEAM_ROLES = ['member', 'admin', 'owner'] as const
export type TeamRole = typeof TEAM_ROLES[number]

export const TEAM_ROLE_INFO: Record<TeamRole, { title: string; text: string; color: string }> = {
  member: { title: 'Member', color: 'info',
            text: "Uses what the team owns (at least Viewer on the team's VEs) and whatever is shared with the team." },
  admin: { title: 'Admin', color: 'primary',
           text: "Invites members, is Admin of the team's VEs and sees its spending." },
  owner: { title: 'Owner', color: 'success',
           text: 'Everything: spending limits, approving purchases, transferring and deleting the team.' },
}
export const teamRoleTitle = (r?: string | null) => TEAM_ROLE_INFO[r as TeamRole]?.title ?? '—'
export const teamRoleAtLeast = (r: string | null | undefined, needed: TeamRole) =>
  TEAM_ROLES.indexOf(r as TeamRole) >= TEAM_ROLES.indexOf(needed)

export interface TeamMember { user_id: string; role: TeamRole; username?: string | null }

export interface Team {
  team_id: string
  name: string
  description: string
  owner_id: string
  members: TeamMember[]
  limits?: { per_admin_cents: number | null; team_cents: number | null }
  created_at: number
  my_role: TeamRole | null
  member_count?: number
  ve_count?: number
  bucket_count?: number
  invitations?: Invitation[]
}

export interface TeamResource {
  kind: 've' | 'bucket'
  id: string
  name: string
  owned: boolean
  role: string
  shared_by?: string | null
}
export const resourceRoleTitle = (r: TeamResource) => (r.kind === 've' ? roleTitle(r.role) : bucketRoleTitle(r.role))
export const resourceLink = (r: TeamResource) => (r.kind === 've' ? `/ves/${r.id}` : bucketLink(r.name))

export interface Invitation {
  id: string
  kind: 'team' | 've' | 'bucket'
  target_id: string
  target_name: string
  role: string
  invitee: { type: 'user' | 'team' | 'email'; id: string; name: string }
  invited_by: string
  invited_by_name: string
  status: 'pending' | 'accepted' | 'rejected' | 'cancelled'
  created_at: number
  answered_at?: number
  signup_code?: string
}

export interface Transfer {
  id: string
  kind: 've' | 'bucket' | 'team'
  target_id: string
  target_name: string
  to: { type: 'user' | 'team'; id: string; name: string }
  from: string
  from_name: string
  status: 'pending' | 'accepted' | 'rejected' | 'cancelled'
  created_at: number
  answered_at?: number
  plan?: { plan_id: string; name: string | null; monthly_fee_cents: number | null }
}

const KIND_TITLES = { team: 'the team', ve: 'the VE', bucket: 'the bucket' } as const

/** "data-team (team)", "VE ml-prod", "bucket ana/sales". */
export const targetTitle = (kind: keyof typeof KIND_TITLES, name: string) => `${KIND_TITLES[kind]} ${name}`

/** The role an invitation gives, in the target's own words. */
export function invitationRoleTitle(inv: Invitation): string {
  if (inv.kind === 'team') return teamRoleTitle(inv.role)
  return inv.kind === 've' ? roleTitle(inv.role) : bucketRoleTitle(inv.role)
}

/** One line saying what accepting does (so nobody accepts blindly). */
export function acceptMeans(inv: Invitation): string {
  if (inv.kind === 'team') {
    return inv.role === 'admin'
      ? `You'll manage ${inv.target_name}'s VEs and members, and see its spending.`
      : `You'll get access to everything ${inv.target_name} owns.`
  }
  const who = inv.invitee.type === 'team' ? `Everyone in ${inv.invitee.name}` : "You"
  const will = who === 'You' ? "'ll" : ' will'
  if (inv.kind === 've') {
    const can = { viewer: 'see', developer: 'see and run things in', admin: 'manage' }[inv.role] ?? 'use'
    return `${who}${will} be able to ${can} the VE ${inv.target_name}.`
  }
  const can = { reader: 'read', writer: 'read and write' }[inv.role] ?? 'use'
  return `${who}${will} be able to ${can} the files of ${inv.target_name}.`
}

/** What accepting a transfer means -- with the money for a VE. */
export function transferMeans(t: Transfer): string {
  const owner = t.to.type === 'team' ? t.to.name : 'you'
  if (t.kind === 've') {
    const fee = t.plan?.monthly_fee_cents
    const pays = fee ? ` and pay its renewals (${formatMoney(fee)} a month, ${t.plan?.name ?? 'its'} plan)` : ''
    return `${owner === 'you' ? 'You' : owner} will own the VE ${t.target_name}${pays}.`
  }
  if (t.kind === 'bucket') return `${owner === 'you' ? 'You' : owner} will own the bucket ${t.target_name} and pay for what it stores.`
  return `You'll become the Owner of the team ${t.target_name}; ${t.from_name} stays an Admin.`
}
