import { JsonRpcGatewayClient } from '@/shared/lib/gateway-protocol'

const DEFAULT_GATEWAY_REQUEST_TIMEOUT_MS = 30_000

export class SpiritAgentGateway extends JsonRpcGatewayClient {
  readonly isProxy = false

  constructor() {
    super({
      closedErrorMessage: 'SpiritAgent gateway connection closed',
      connectErrorMessage: 'Could not connect to SpiritAgent gateway',
      createRequestId: (nextId: number) => nextId,
      notConnectedErrorMessage: 'SpiritAgent gateway is not connected',
      requestTimeoutMs: DEFAULT_GATEWAY_REQUEST_TIMEOUT_MS
    })
  }
}
