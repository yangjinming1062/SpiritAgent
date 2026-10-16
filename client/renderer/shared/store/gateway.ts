import { atom } from 'nanostores'

import type { ConnectionState, RpcRequester } from '@/shared/lib/gateway-protocol'

export interface SpiritAgentGatewayLike {
  readonly connectionState: ConnectionState
  readonly isPresentationHost: boolean
  request: RpcRequester
}

// 所有窗口通过主进程 IPC 访问同一账户连接。
export const $gateway = atom<SpiritAgentGatewayLike | null>(null)

export const $gatewayState = atom<ConnectionState>('idle')
export const $gatewayActiveToolCount = atom(0)

export function setPrimaryGateway(gateway: SpiritAgentGatewayLike | null): void {
  $gateway.set(gateway)
  $gatewayState.set(gateway?.connectionState ?? 'closed')
  $gatewayActiveToolCount.set(0)
}

export function reportPrimaryGatewayState(state: ConnectionState, activeToolCount: number): void {
  $gatewayState.set(state)
  $gatewayActiveToolCount.set(activeToolCount)
}
