import test from 'node:test'
import assert from 'node:assert/strict'

import { deriveDashboardStatus, toneForScore, statusTextFor, UNKNOWN_SCORE_TEXT } from '../src/utils/dashboardStatus.js'

test('拿到后端评分：按分数分档，展示真实数字', () => {
  const ok = deriveDashboardStatus({ dashboard: { score: 96, status: 'ok' }, backendOnline: true })
  assert.equal(ok.available, true)
  assert.equal(ok.degraded, false)
  assert.equal(ok.score, 96)
  assert.equal(ok.scoreText, '96%')
  assert.equal(ok.orbPct, 96)
  assert.equal(ok.tone, 'ok')
  assert.equal(ok.statusText, '运行正常')

  assert.equal(deriveDashboardStatus({ dashboard: { score: 80 } }).tone, 'warn')
  assert.equal(deriveDashboardStatus({ dashboard: { score: 42 } }).tone, 'danger')
})

test('分数为 0 是真实的坏分数，不能被当成"没有数据"', () => {
  const zero = deriveDashboardStatus({ dashboard: { score: 0, status: 'critical' }, backendOnline: true })
  assert.equal(zero.available, true)
  assert.equal(zero.score, 0)
  assert.equal(zero.scoreText, '0%')
  assert.equal(zero.tone, 'danger')
  assert.equal(zero.statusText, '需要处理')
})

test('核心回归：后端在线但 /dashboard 取数失败时，不得再显示 92% 与"运行正常"', () => {
  // 修复前：score = backendOnline ? (dashboard?.score ?? 92) : 60 → 92，statusText 取 '运行正常'
  const failed = deriveDashboardStatus({ dashboard: null, backendOnline: true, error: 'Request failed with status code 500' })
  assert.equal(failed.available, false)
  assert.equal(failed.degraded, true)
  assert.equal(failed.score, null, '没有数据时不得编造分数')
  assert.equal(failed.scoreText, UNKNOWN_SCORE_TEXT)
  assert.notEqual(failed.scoreText, '92%')
  assert.equal(failed.tone, 'warn', '数据不可用应给出告警色，而不是绿色')
  assert.equal(failed.statusText, '数据不可用')
  assert.notEqual(failed.statusText, '运行正常')
  assert.deepEqual(failed.tags, ['工作台数据获取失败'])
  assert.match(failed.note, /500/)
})

test('后端在线但后端未返回评分：同样按缺失处理，并保留服务端结论', () => {
  const noScore = deriveDashboardStatus({ dashboard: { status: 'attention', metrics: { servers: 78 } }, backendOnline: true })
  assert.equal(noScore.available, false)
  assert.equal(noScore.degraded, true)
  assert.equal(noScore.score, null)
  assert.equal(noScore.statusText, '需要关注', '服务端 status 仍可给出结论')
  assert.deepEqual(noScore.tags, ['健康评分缺失'])

  const nonsense = deriveDashboardStatus({ dashboard: { score: 'high' }, backendOnline: true })
  assert.equal(nonsense.available, false, '非数字评分不能直接渲染')
  assert.equal(nonsense.statusText, '数据不完整')
})

test('后端离线：分数不展示，色调 danger，明确标注后端离线', () => {
  const offline = deriveDashboardStatus({ dashboard: { score: 99 }, backendOnline: false })
  assert.equal(offline.available, false)
  assert.equal(offline.score, null)
  assert.equal(offline.scoreText, UNKNOWN_SCORE_TEXT)
  assert.equal(offline.tone, 'danger')
  assert.equal(offline.statusText, '后端离线')
  assert.deepEqual(offline.tags, ['后端离线'])
})

test('加载中不显示任何结论，避免闪一下"数据不可用"', () => {
  const loading = deriveDashboardStatus({ dashboard: null, backendOnline: true, loading: true })
  assert.equal(loading.available, false)
  assert.equal(loading.degraded, false)
  assert.equal(loading.statusText, '加载中')
  assert.equal(loading.tone, 'neutral')
})

test('toneForScore / statusTextFor 边界', () => {
  assert.equal(toneForScore(90, true), 'ok')
  assert.equal(toneForScore(89.9, true), 'warn')
  assert.equal(toneForScore(70, true), 'warn')
  assert.equal(toneForScore(69.9, true), 'danger')
  assert.equal(toneForScore(100, false), 'danger')
  assert.equal(toneForScore(null, true), 'warn')
  assert.equal(toneForScore(undefined, true), 'warn')
  assert.equal(toneForScore(NaN, true), 'warn')

  assert.equal(statusTextFor('critical'), '需要处理')
  assert.equal(statusTextFor('ATTENTION'), '需要关注')
  assert.equal(statusTextFor('healthy'), '运行正常')
  assert.equal(statusTextFor(''), '')
  assert.equal(statusTextFor(undefined), '')
})
