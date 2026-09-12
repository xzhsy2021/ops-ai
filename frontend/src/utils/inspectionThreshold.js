/**
 * 巡检自定义规则"阈值判定"的展示辅助（2026-09 复盘第 11 轮）。
 *
 * 背景：规则编辑器的三个阈值标签此前硬编码为 `HIGH ≥ / MEDIUM ≥ / LOW ≥`，
 * 与"比较符"下拉（> / >= / < / <= / == / contains）毫无关系。用户把比较符改成
 * `<` 之后标签仍然写着 `≥`，很容易按相反方向填阈值——而后端 `_evaluate_threshold`
 * 对 `<`/`<=` 的语义是"按顺序首个命中的档位"，也就是要求**升序**填写
 * （high < medium < low），填反了不会报错但判定方向完全相反。
 *
 * 这里只做展示层归一：标签跟随比较符，并给出填写顺序提示。
 */

export const COMPARATOR_SYMBOLS = {
  '>': '>',
  '>=': '≥',
  '<': '<',
  '<=': '≤',
  '==': '=',
  contains: '包含',
}

export const THRESHOLD_ORDER_HINTS = {
  desc: '降序填写：HIGH > MEDIUM > LOW（如 90 / 75 / 50）',
  asc: '升序填写：HIGH < MEDIUM < LOW（如 10 / 20 / 30）',
  equal: '按命中值填写：等于该值即为对应等级',
  contains: '填写关键字文本：命中即为对应等级',
}

/** 比较符 → 展示符号（未知/空值按历史默认 `>`）。 */
export function comparatorSymbol(comparator) {
  const key = String(comparator === undefined || comparator === null ? '>' : comparator).trim() || '>'
  return COMPARATOR_SYMBOLS[key] || '>'
}

/** 阈值填写顺序提示：`<`/`<=` 是升序，`>`/`>=` 是降序。 */
export function thresholdOrderHint(comparator) {
  const key = String(comparator === undefined || comparator === null ? '>' : comparator).trim() || '>'
  if (key === '<' || key === '<=') return THRESHOLD_ORDER_HINTS.asc
  if (key === '==') return THRESHOLD_ORDER_HINTS.equal
  if (key === 'contains') return THRESHOLD_ORDER_HINTS.contains
  return THRESHOLD_ORDER_HINTS.desc
}

/** 三个阈值标签：HIGH/MEDIUM/LOW + 当前比较符符号。 */
export function thresholdLabels(comparator) {
  const symbol = comparatorSymbol(comparator)
  return {
    high: `HIGH ${symbol}`,
    medium: `MEDIUM ${symbol}`,
    low: `LOW ${symbol}`,
  }
}
