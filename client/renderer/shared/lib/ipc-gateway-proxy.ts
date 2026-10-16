import {
  type ConnectionState,
  type RpcMethod,
  type RpcRequest,
  type RpcResponse,
  SpiritAgentRpcError
} from '@/shared/lib/gateway-protocol'
import { gatewayApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $gatewayState, type SpiritAgentGatewayLike } from '@/shared/store/gateway'

export class IpcGatewayProxy implements SpiritAgentGatewayLike {
  constructor(readonly isPresentationHost = false) {}

  get connectionState(): ConnectionState {
    return $gatewayState.get()
  }

  async request<K extends RpcMethod>(method: K, params: RpcRequest<K>): Promise<RpcResponse<K>> {
    if (!gatewayApi()?.gatewayRequest) {
      throw new Error('SpiritAgent desktop IPC is unavailable')
    }

    const auth = $auth.get()

    if (auth.kind !== 'authenticated') {
      throw new Error('An authenticated gateway is required')
    }

    const authSessionId = auth.snapshot.sessionId
    const response = await gatewayApi().gatewayRequest({ authSessionId, method, params })
    const current = $auth.get()

    if (current.kind !== 'authenticated' || current.snapshot.sessionId !== authSessionId) {
      throw new Error('Gateway authentication changed')
    }

    if (response.ok) {
      return response.result
    }

    const { code, data, message } = response.error

    if (code !== undefined) {
      throw new SpiritAgentRpcError(code, message, data)
    }

    throw new Error(message)
  }
}
