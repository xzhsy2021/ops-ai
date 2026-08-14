import { Children, useState } from 'react'
import type { CSSProperties, KeyboardEvent, ReactNode } from 'react'
import {
  APPROVER_CHANNELS,
  approverKey,
  approverLabel,
} from '../utils/approverIdentities.js'
import type { ApproverChannel, ApproverIdentity } from '../utils/approverIdentities.js'

/** 会话/房间绑定：{channel, channel_account_id, conversation_id}，与后端 normalize_conversation_binding 对齐。 */
export type ConversationBinding = {
  channel: string
  channel_account_id?: string
  conversation_id?: string
}

const CHANNEL_LABELS: Record<string, string> = {
  matrix: 'Matrix',
  wechat: '微信',
  telegram: 'Telegram',
}

const inputStyle: CSSProperties = {
  width: '100%',
  padding: '6px 10px',
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-strong)',
  borderRadius: '6px',
  color: 'var(--text-primary)',
  fontSize: '13px',
}

function ChipRow({ children, emptyText }: { children: ReactNode; emptyText: string }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {children}
      {Children.count(children) === 0 && (
        <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{emptyText}</span>
      )}
    </div>
  )
}

function Chip({
  label,
  title,
  onRemove,
}: {
  label: string
  title?: string
  onRemove?: () => void
}) {
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '2px 8px', borderRadius: 12,
        background: 'var(--bg-hover)', fontSize: 12, fontFamily: 'monospace',
      }}
      title={title || label}
    >
      {label}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--text-muted)', padding: 0, fontSize: 14,
          }}
        >
          ×
        </button>
      )}
    </span>
  )
}

function enterOrComma(e: KeyboardEvent<HTMLInputElement>, commit: () => void) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault()
    commit()
  }
}

function ChannelSelect({
  value,
  onChange,
  width = '120px',
}: {
  value: string
  onChange: (v: string) => void
  width?: string
}) {
  return (
    <select
      aria-label="渠道"
      style={{ ...inputStyle, width }}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {APPROVER_CHANNELS.map((c) => (
        <option key={c} value={c}>{CHANNEL_LABELS[c] || c}</option>
      ))}
    </select>
  )
}

/**
 * 审批人编辑器：渠道 + 渠道账号 + 发送者 ID + chips。
 * 数据即 ApproverIdentity[]，与系统级 message_routing.approvers、
 * token 级 approver_identities 共用同一结构（后端 normalize_identity 对齐）。
 */
export function ApproverEditor({
  value,
  onChange,
  hint,
  emptyText = '未配置（回退到系统/服务级设置；均未配置时同房间任意成员可审批）',
}: {
  value: ApproverIdentity[]
  onChange: (v: ApproverIdentity[]) => void
  hint?: string
  emptyText?: string
}) {
  const [channel, setChannel] = useState<ApproverChannel>('matrix')
  const [account, setAccount] = useState('default')
  const [sender, setSender] = useState('')

  const add = () => {
    const identity: ApproverIdentity = {
      channel,
      channel_account_id: account.trim() || 'default',
      sender_id: sender.trim(),
    }
    if (!identity.sender_id) return
    if (value.some((item) => approverKey(item) === approverKey(identity))) return
    onChange([...value, identity])
    setSender('')
  }

  const remove = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx))
  }

  return (
    <div>
      {hint && <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 6 }}>{hint}</div>}
      <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
        <ChannelSelect value={channel} onChange={(v) => setChannel(v as ApproverChannel)} />
        <input
          aria-label="渠道账号"
          style={{ ...inputStyle, flex: '0 0 120px' }}
          value={account}
          onChange={(e) => setAccount(e.target.value)}
          placeholder="default"
        />
        <input
          aria-label="发送者 ID"
          style={{ ...inputStyle, flex: '1 1 240px' }}
          value={sender}
          onChange={(e) => setSender(e.target.value)}
          onKeyDown={(e) => enterOrComma(e, add)}
          placeholder={channel === 'matrix' ? '@user:example.org' : '发送者 ID'}
        />
        <button className="btn" onClick={add} type="button" title="添加审批人" aria-label="添加审批人">+</button>
      </div>
      <ChipRow emptyText={emptyText}>
        {value.map((item, i) => (
          <Chip
            key={approverKey(item)}
            label={approverLabel(item)}
            title={approverLabel(item)}
            onRemove={() => remove(i)}
          />
        ))}
      </ChipRow>
    </div>
  )
}

/**
 * 会话/房间绑定编辑器：渠道 + 渠道账号 + 会话 ID + chips。
 * 数据即 ConversationBinding[]，与后端 normalize_conversation_binding 对齐。
 */
export function RoomEditor({
  value,
  onChange,
  hint,
  emptyText = '未绑定（不限制房间）',
  placeholder = '如：!opsRoom:matrix.org',
}: {
  value: ConversationBinding[]
  onChange: (v: ConversationBinding[]) => void
  hint?: string
  emptyText?: string
  placeholder?: string
}) {
  const [channel, setChannel] = useState<string>('matrix')
  const [account, setAccount] = useState('default')
  const [conversation, setConversation] = useState('')

  const add = () => {
    const conversationId = conversation.trim()
    if (!conversationId) return
    const binding: ConversationBinding = {
      channel,
      channel_account_id: account.trim() || 'default',
      conversation_id: conversationId,
    }
    const key = `${binding.channel}\u0000${binding.channel_account_id}\u0000${binding.conversation_id}`
    if (value.some((item) => `${item.channel}\u0000${item.channel_account_id || ''}\u0000${item.conversation_id || ''}` === key)) return
    onChange([...value, binding])
    setConversation('')
  }

  const remove = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx))
  }

  return (
    <div>
      {hint && <div style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 6 }}>{hint}</div>}
      <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
        <ChannelSelect value={channel} onChange={setChannel} />
        <input
          aria-label="渠道账号"
          style={{ ...inputStyle, flex: '0 0 120px' }}
          value={account}
          onChange={(e) => setAccount(e.target.value)}
          placeholder="default"
        />
        <input
          aria-label="会话 ID"
          style={{ ...inputStyle, flex: '1 1 240px' }}
          value={conversation}
          onChange={(e) => setConversation(e.target.value)}
          onKeyDown={(e) => enterOrComma(e, add)}
          placeholder={placeholder}
        />
        <button className="btn" onClick={add} type="button" title="添加会话绑定" aria-label="添加会话绑定">+</button>
      </div>
      <ChipRow emptyText={emptyText}>
        {value.map((item, i) => {
          const label = `${item.channel}/${item.channel_account_id || 'default'}: ${item.conversation_id || ''}`
          return (
            <Chip
              key={`${item.channel}\u0000${item.channel_account_id || ''}\u0000${item.conversation_id || ''}`}
              label={label}
              title={label}
              onRemove={() => remove(i)}
            />
          )
        })}
      </ChipRow>
    </div>
  )
}
