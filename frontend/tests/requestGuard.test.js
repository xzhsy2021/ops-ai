import test from 'node:test'
import assert from 'node:assert/strict'

import { createRequestGuard } from '../src/utils/requestGuard.js'

test('只有最新的请求结果会被采用，旧响应一律丢弃', () => {
  const guard = createRequestGuard()

  const first = guard.begin()   // 用户输入 "o"：请求 1
  const second = guard.begin()  // 用户输入 "op"：请求 2
  const third = guard.begin()   // 用户输入 "ops"：请求 3

  // 乱序到达：请求 1 最后返回
  assert.equal(guard.isCurrent(first), false, '最早的请求结果必须被丢弃')
  assert.equal(guard.isCurrent(second), false)
  assert.equal(guard.isCurrent(third), true, '最新请求结果才允许写入列表')
})

test('相邻两次请求：先发的立即失效', () => {
  const guard = createRequestGuard()
  const a = guard.begin()
  assert.equal(guard.isCurrent(a), true)
  const b = guard.begin()
  assert.equal(guard.isCurrent(a), false, '新请求发出后，旧 token 立刻失效')
  assert.equal(guard.isCurrent(b), true)
  assert.equal(guard.current(), b)
})

test('各实例互不影响（每个页面/每个请求源独立计时）', () => {
  const pageA = createRequestGuard()
  const pageB = createRequestGuard()
  const tokenA = pageA.begin()
  pageB.begin()
  pageB.begin()
  assert.equal(pageA.isCurrent(tokenA), true)
})

test('未 begin 过的 token 视为陈旧', () => {
  const guard = createRequestGuard()
  assert.equal(guard.isCurrent(0), true, '初始 latest=0，token 0 即"尚未发过请求"')
  guard.begin()
  assert.equal(guard.isCurrent(0), false)
})
