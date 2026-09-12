import test from 'node:test'
import assert from 'node:assert/strict'

import {
  comparatorSymbol,
  thresholdOrderHint,
  thresholdLabels,
  COMPARATOR_SYMBOLS,
} from '../src/utils/inspectionThreshold.js'

test('符号映射覆盖后端白名单的全部 6 个比较符', () => {
  // 后端 SUPPORTED_COMPARATORS：> >= < <= == contains（不多不少）
  assert.deepEqual(
    Object.keys(COMPARATOR_SYMBOLS).sort(),
    ['>', '>=', '<', '<=', '==', 'contains'].sort(),
  )
  assert.equal(comparatorSymbol('>'), '>')
  assert.equal(comparatorSymbol('>='), '≥')
  assert.equal(comparatorSymbol('<'), '<')
  assert.equal(comparatorSymbol('<='), '≤')
  assert.equal(comparatorSymbol('=='), '=')
  assert.equal(comparatorSymbol('contains'), '包含')
})

test('缺省/未知比较符回落到 >（与后端默认一致）', () => {
  assert.equal(comparatorSymbol(undefined), '>')
  assert.equal(comparatorSymbol(null), '>')
  assert.equal(comparatorSymbol(''), '>')
  assert.equal(comparatorSymbol('  '), '>')
  assert.equal(comparatorSymbol('gte'), '>')
})

test('阈值标签跟随比较符（RED：修前硬编码 HIGH ≥ / MEDIUM ≥ / LOW ≥）', () => {
  assert.deepEqual(thresholdLabels('<'), { high: 'HIGH <', medium: 'MEDIUM <', low: 'LOW <' })
  assert.deepEqual(thresholdLabels('<='), { high: 'HIGH ≤', medium: 'MEDIUM ≤', low: 'LOW ≤' })
  assert.deepEqual(thresholdLabels('>='), { high: 'HIGH ≥', medium: 'MEDIUM ≥', low: 'LOW ≥' })
  assert.deepEqual(thresholdLabels('=='), { high: 'HIGH =', medium: 'MEDIUM =', low: 'LOW =' })
  assert.deepEqual(thresholdLabels(undefined), { high: 'HIGH >', medium: 'MEDIUM >', low: 'LOW >' })
})

test('填写顺序提示：</<= 必须升序，>/>= 降序', () => {
  assert.match(thresholdOrderHint('<'), /升序/)
  assert.match(thresholdOrderHint('<='), /升序/)
  assert.match(thresholdOrderHint('>'), /降序/)
  assert.match(thresholdOrderHint('>='), /降序/)
  assert.match(thresholdOrderHint(undefined), /降序/)
  assert.match(thresholdOrderHint('=='), /等于/)
  assert.match(thresholdOrderHint('contains'), /关键字/)
})
