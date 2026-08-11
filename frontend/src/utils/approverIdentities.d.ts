export type ApproverChannel = 'matrix' | 'wechat' | 'telegram'

export interface ApproverIdentity {
  channel: ApproverChannel
  channel_account_id: string
  sender_id: string
}

export const APPROVER_CHANNELS: readonly ApproverChannel[]
export function approverKey(identity: ApproverIdentity): string
export function approverLabel(identity: ApproverIdentity): string
export function normalizeApprovers(value: unknown): ApproverIdentity[]
export function withStructuredApprovers<T extends object>(
  routing: T,
): Omit<T, 'approvers'> & { approvers: ApproverIdentity[] }
