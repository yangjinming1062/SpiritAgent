import type { ConnectionState } from '@/shared/lib/gateway-protocol'
import { $gatewayState, type SpiritAgentGatewayLike } from '@/shared/store/gateway'

export class IpcGatewayProxy implements SpiritAgentGatewayLike {
  readonly isProxy = true

  get connectionState(): ConnectionState {
    return $gatewayState.get()
  }

  async request<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!window.spiritagent?.gatewayRequest) {
      throw new Error('SpiritAgent desktop IPC is unavailable')
    }

    return await window.spiritagent.gatewayRequest<T>({ method, params })
  }

  close(): void {
    // 代理不主动拆除底层的长连接 WebSocket
  }

  resetSeq(_seq: number): void {
    // 序列号由 Host 端统一维持与去重
  }
}
