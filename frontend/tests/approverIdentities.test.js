import test from 'node:test'
import assert from 'node:assert/strict'

import {
  approverKey,
  approverLabel,
  normalizeApprovers,
  withStructuredApprovers,
} from '../src/utils/approverIdentities.js'


test('normalizes legacy Matrix strings and deduplicates complete identities', () => {
  assert.deepEqual(normalizeApprovers([
    ' @owner:example.org ',
    { channel: 'matrix', channel_account_id: 'default', sender_id: '@owner:example.org' },
  ]), [{
    channel: 'matrix',
    channel_account_id: 'default',
    sender_id: '@owner:example.org',
  }])
})


test('keeps valid generic identities and drops malformed values', () => {
  assert.deepEqual(normalizeApprovers([
    { channel: 'wechat', channel_account_id: 'work', sender_id: 'wx-1' },
    { channel: 'telegram', channel_account_id: 'default', sender_id: 'tg-1' },
    { channel: 'signal', channel_account_id: 'default', sender_id: 'bad' },
    { channel: 'wechat', channel_account_id: '', sender_id: 'bad' },
    null,
    42,
  ]), [
    { channel: 'wechat', channel_account_id: 'work', sender_id: 'wx-1' },
    { channel: 'telegram', channel_account_id: 'default', sender_id: 'tg-1' },
  ])
})


test('keys all identity fields and formats a safe text label', () => {
  const identity = { channel: 'wechat', channel_account_id: 'work', sender_id: '<owner>' }
  assert.notEqual(approverKey(identity), approverKey({ ...identity, channel_account_id: 'other' }))
  assert.equal(approverLabel(identity), 'wechat/work: <owner>')
})


test('serializes submitted routing payload with structured approvers', () => {
  const routing = withStructuredApprovers({
    enabled: true,
    aliases: [],
    keywords: ['risk'],
    priority: 10,
    approvers: ['@legacy:example.org'],
  })
  assert.deepEqual(routing.approvers, [{
    channel: 'matrix',
    channel_account_id: 'default',
    sender_id: '@legacy:example.org',
  }])
})
