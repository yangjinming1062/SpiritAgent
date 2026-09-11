import { atom } from 'nanostores'

import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import type { SpiritAgentGateway } from '@/shared/spiritagent'

export interface SpiritAgentGatewayLike {
  readonly connectionState: ConnectionState
  readonly isProxy?: boolean
  close?: () => void
  connect?: (url: string) => Promise<void>
  request: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  resetSeq?: (seq: number) => void
}

// 唯一的主网关——桌面端只与一个后端通信。

// 当前激活的网关实例。供内联消息流组件（如模型浮层）使用——
// 它们调用网关方法时不希望把实例通过 props 一路向下透传。
export const $gateway = atom<SpiritAgentGateway | SpiritAgentGatewayLike | null>(null)

// 当前网关的 WS 实时状态。被网关 hooks 和连接中浮层消费；
// 由本模块持有（而非会话 store），因为它描述的是单条后端链路，
// 与任何对话无关。
export const $gatewayState = atom<ConnectionState>('idle')

function setGatewayState(next: ConnectionState): void {
  $gatewayState.set(next)
}

export function setPrimaryGateway(gateway: SpiritAgentGateway | SpiritAgentGatewayLike | null): void {
  $gateway.set(gateway)
  setGatewayState(gateway?.connectionState ?? 'closed')
}

// 关闭当前网关（同步拆除 WS，避免 401 触发的登出要等 TCP 超时）并清空 atom。
// `auth.ts::logout` 和 `use-gateway-boot.ts` 的清理都依赖这一对操作；
// 集中在一处可保证「先关后清」这一顺序永远不会被拆开。
export function tearDownPrimaryGateway(): void {
  $gateway.get()?.close?.()
  setPrimaryGateway(null)
}

export function reportPrimaryGatewayState(state: ConnectionState): void {
  setGatewayState(state)
}
