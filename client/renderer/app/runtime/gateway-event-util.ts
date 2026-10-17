/** 路由守卫计算出的窗口角色上下文，随事件传给各能力处理器。 */
export interface EventRouteContext {
  /** 精灵视图负责账户级呈现统计与协调。 */
  isPresentationHost: boolean
}
