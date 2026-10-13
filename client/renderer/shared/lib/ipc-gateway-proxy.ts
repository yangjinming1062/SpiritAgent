import { type ConnectionState, SpiritAgentRpcError } from '@/shared/lib/gateway-protocol'
import { gatewayApi } from '@/shared/platform/desktop'
import { $gatewayState, type SpiritAgentGatewayLike } from '@/shared/store/gateway'

export class IpcGatewayProxy implements SpiritAgentGatewayLike {
  readonly isProxy = true

  get connectionState(): ConnectionState {
    return $gatewayState.get()
  }

  async request<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!gatewayApi()?.gatewayRequest) {
      throw new Error('SpiritAgent desktop IPC is unavailable')
    }

    const response = await gatewayApi().gatewayRequest({ method, params })

    if (response.ok) {
      return response.result as T
    }

    const { code, data, message } = response.error

    if (code !== undefined) {
      throw new SpiritAgentRpcError(code, message, data)
    }

    throw new Error(message)
  }
}
