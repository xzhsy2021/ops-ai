import test from 'node:test'
import assert from 'node:assert/strict'

import {
  backendTimeValue,
  formatDateTime,
  formatDay,
  formatTime,
  parseBackendTime,
  relativeFromNow,
} from '../src/utils/datetime.js'

test('naive 后端时间戳按 UTC 解析，而不是被浏览器当成本地时间', () => {
  // 核心契约：两种落库格式（空格分隔的 SQLAlchemy DateTime、手写 isoformat）都视为 UTC
  assert.equal(parseBackendTime('2026-09-12 13:41:21')?.toISOString(), '2026-09-12T13:41:21.000Z')
  assert.equal(parseBackendTime('2026-09-12T13:41:21.054406')?.toISOString(), '2026-09-12T13:41:21.054Z')
  assert.equal(backendTimeValue('2026-09-12 13:41:21'), Date.UTC(2026, 8, 12, 13, 41, 21))
})

test('已带时区标记的时间戳保持原语义', () => {
  assert.equal(parseBackendTime('2026-09-12T13:41:21Z')?.toISOString(), '2026-09-12T13:41:21.000Z')
  assert.equal(parseBackendTime('2026-09-12T13:41:21+02:00')?.toISOString(), '2026-09-12T11:41:21.000Z')
})

test('无法识别的时间串返回 null，展示层回退原文', () => {
  assert.equal(parseBackendTime('21:41:33'), null) // agent 日志行只有时间部分
  assert.equal(parseBackendTime(''), null)
  assert.equal(parseBackendTime(null), null)
  assert.equal(parseBackendTime('not-a-date'), null)
  assert.equal(parseBackendTime('2026-13-45 99:99:99'), null)
  assert.equal(formatTime('21:41:33'), '21:41:33')
  assert.equal(formatTime(undefined), '-')
  assert.equal(backendTimeValue('21:41:33'), 0)
})

test('本地展示随浏览器时区换算，且与 UTC 文本不同（非 UTC 机器）', () => {
  const value = '2026-09-12 13:41:21'
  const parsed = parseBackendTime(value)
  assert.ok(parsed)
  const pad = (n) => String(n).padStart(2, '0')
  const expectedDay = `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}`
  const expectedDateTime = `${expectedDay} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
  assert.equal(formatDay(value), expectedDay)
  assert.equal(formatDateTime(value), expectedDateTime)
  if (new Date().getTimezoneOffset() !== 0) {
    // 旧实现直接 new Date('2026-09-12 13:41:21')（当本地时间）会得到不同的日期/时间
    assert.notEqual(formatDateTime(value), '2026-09-12 13:41')
  }
})

test('相对时间是按真实时差算的：30 秒前就是"刚刚"', () => {
  const recent = new Date(Date.now() - 30_000).toISOString().slice(0, 19).replace('T', ' ')
  assert.equal(relativeFromNow(recent), '刚刚')
  const twoHours = new Date(Date.now() - 2 * 3600_000).toISOString().slice(0, 19).replace('T', ' ')
  assert.equal(relativeFromNow(twoHours), '2 小时前')
  const future = new Date(Date.now() + 60_000).toISOString().slice(0, 19).replace('T', ' ')
  assert.equal(relativeFromNow(future), '')
  assert.equal(relativeFromNow('21:41:33'), '')
})
