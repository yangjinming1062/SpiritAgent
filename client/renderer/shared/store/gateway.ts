import { atom } from 'nanostores'

import type { ConnectionState } from '@/shared/lib/gateway-protocol'

export interface SpiritAgentGatewayLike {
  readonly connectionState: ConnectionState
  readonly isProxy: boolean
  close?: () => void
  request: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
}

// 各窗持有宿主连接或 IPC 代理，业务请求只消费公共请求能力。
export const $gateway = atom<SpiritAgentGatewayLike | null>(null)

export const $gatewayState = atom<ConnectionState>('idle')

export function setPrimaryGateway(gateway: SpiritAgentGatewayLike | null): void {
  $gateway.set(gateway)
  $gatewayState.set(gateway?.connectionState ?? 'closed')
}

// 先断开连接再清空引用，阻止旧会话继续收发。
export function tearDownPrimaryGateway(): void {
  $gateway.get()?.close?.()
  setPrimaryGateway(null)
}

export function reportPrimaryGatewayState(state: ConnectionState): void {
  $gatewayState.set(state)
}
