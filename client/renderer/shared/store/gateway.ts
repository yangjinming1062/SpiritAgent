import { atom } from 'nanostores'

import type { ConnectionState } from '@/shared/lib/gateway-protocol'

export interface SpiritAgentGatewayLike {
  readonly isPresentationHost: boolean
}

// 所有窗口通过主进程 IPC 访问同一账户连接。
export const $gateway = atom<SpiritAgentGatewayLike | null>(null)

export const $gatewayState = atom<ConnectionState>('idle')
export const $gatewayActiveToolCount = atom(0)

export function setPrimaryGateway(gateway: SpiritAgentGatewayLike | null): void {
  $gateway.set(gateway)
  // 连接状态的真源是 $gatewayState；网关对象只表达"是否已装配"，断开时才回落到 closed。
  $gatewayState.set(gateway ? $gatewayState.get() : 'closed')
  $gatewayActiveToolCount.set(0)
}

export function reportPrimaryGatewayState(state: ConnectionState, activeToolCount: number): void {
  $gatewayState.set(state)
  $gatewayActiveToolCount.set(activeToolCount)
}
