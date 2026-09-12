export const COMPARATOR_SYMBOLS: Record<string, string>

export const THRESHOLD_ORDER_HINTS: {
  desc: string
  asc: string
  equal: string
  contains: string
}

/** 比较符 → 展示符号（未知/空值按历史默认 `>`）。 */
export function comparatorSymbol(comparator?: string | null): string

/** 阈值填写顺序提示：`<`/`<=` 是升序，`>`/`>=` 是降序。 */
export function thresholdOrderHint(comparator?: string | null): string

/** 三个阈值标签：HIGH/MEDIUM/LOW + 当前比较符符号。 */
export function thresholdLabels(comparator?: string | null): { high: string; medium: string; low: string }
