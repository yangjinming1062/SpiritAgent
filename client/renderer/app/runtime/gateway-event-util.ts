/** 网关事件 payload 窄解码入口：非对象回落空对象，字段由调用方按需读取。 */
export function decodePayload<T extends object>(payload: unknown): Partial<T> {
  return payload && typeof payload === 'object' && !Array.isArray(payload) ? (payload as Partial<T>) : {}
}

/** 路由守卫计算出的窗口角色上下文，随事件传给各能力处理器。 */
export interface EventRouteContext {
  /** 精灵视图负责账户级呈现统计与协调。 */
  isPresentationHost: boolean
}
