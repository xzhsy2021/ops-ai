/**
 * 请求时序守卫（纯函数工厂，便于单测）。
 *
 * 背景（2026-09-12 复盘第 9 轮）：列表页在筛选输入变化时逐次发请求，
 * 响应到达顺序不保证与请求顺序一致 —— 慢的旧请求后到会把新结果覆盖掉，
 * 用户看到的是上一个关键字/上一页的陈旧数据，且没有任何提示。
 *
 * 用法：
 *   const guard = createRequestGuard()
 *   const token = guard.begin()
 *   const res = await api(...)
 *   if (!guard.isCurrent(token)) return   // 已有更新的请求发出，丢弃本次结果
 */

export function createRequestGuard() {
  let latest = 0
  return {
    /** 开始一次新请求，返回本次的 token（同时让此前所有请求失效） */
    begin() {
      latest += 1
      return latest
    },
    /** 该 token 是否仍是最后一次发出的请求 */
    isCurrent(token) {
      return token === latest
    },
    /** 当前最新 token（调试/断言用） */
    current() {
      return latest
    },
  }
}
