/** 网关事件 payload 窄解码入口：非对象回落空对象，字段由调用方按需读取。 */
export function decodePayload<T extends object>(payload: unknown): Partial<T> {
  return payload && typeof payload === 'object' && !Array.isArray(payload) ? (payload as Partial<T>) : {}
}

/** 路由守卫计算出的窗口角色上下文，随事件传给各能力处理器。 */
export interface EventRouteContext {
  /** 本窗口是否为主进程代理（生活空间 / 工作台）：代理不执行宿主专属副作用。 */
  isProxy: boolean
  /** 是否允许本窗口播放语音：代理窗口始终允许，宿主窗在聊天面板可见时静音。 */
  shouldPlayAudio: boolean
}
