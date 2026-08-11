/** @typedef {'matrix' | 'wechat' | 'telegram'} ApproverChannel */

/**
 * @typedef {object} ApproverIdentity
 * @property {ApproverChannel} channel
 * @property {string} channel_account_id
 * @property {string} sender_id
 */

/** @type {readonly ApproverChannel[]} */
export const APPROVER_CHANNELS = Object.freeze(['matrix', 'wechat', 'telegram'])

/** @param {ApproverIdentity} identity */
export const approverKey = (identity) =>
  `${identity.channel}\u0000${identity.channel_account_id}\u0000${identity.sender_id}`

/** @param {ApproverIdentity} identity */
export const approverLabel = (identity) =>
  `${identity.channel}/${identity.channel_account_id}: ${identity.sender_id}`

/**
 * Normalize legacy Matrix sender IDs and generic channel identities.
 * Invalid values are ignored at this UI boundary; the API remains fail closed.
 * @param {unknown} value
 * @returns {ApproverIdentity[]}
 */
export function normalizeApprovers(value) {
  if (!Array.isArray(value)) return []
  /** @type {ApproverIdentity[]} */
  const result = []
  const seen = new Set()
  value.forEach((item) => {
    /** @type {ApproverIdentity | null} */
    let identity = null
    if (typeof item === 'string' && item.trim()) {
      identity = {
        channel: 'matrix',
        channel_account_id: 'default',
        sender_id: item.trim(),
      }
    } else if (item && typeof item === 'object') {
      const candidate = /** @type {Record<string, unknown>} */ (item)
      const channel = candidate.channel
      const account = typeof candidate.channel_account_id === 'string'
        ? candidate.channel_account_id.trim()
        : ''
      const sender = typeof candidate.sender_id === 'string' ? candidate.sender_id.trim() : ''
      if (APPROVER_CHANNELS.includes(/** @type {ApproverChannel} */ (channel)) && account && sender) {
        identity = {
          channel: /** @type {ApproverChannel} */ (channel),
          channel_account_id: account,
          sender_id: sender,
        }
      }
    }
    if (identity && !seen.has(approverKey(identity))) {
      seen.add(approverKey(identity))
      result.push(identity)
    }
  })
  return result
}

/**
 * @template {Record<string, unknown>} T
 * @param {T} routing
 * @returns {Omit<T, 'approvers'> & {approvers: ApproverIdentity[]}}
 */
export function withStructuredApprovers(routing) {
  return {
    ...routing,
    approvers: normalizeApprovers(routing.approvers),
  }
}
