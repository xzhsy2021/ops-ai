export type RequestGuard = {
  /** 开始一次新请求，返回本次 token（让此前所有请求失效） */
  begin(): number
  /** 该 token 是否仍是最后一次发出的请求 */
  isCurrent(token: number): boolean
  current(): number
}

export function createRequestGuard(): RequestGuard
