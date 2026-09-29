import { JsonRpcGatewayClient } from '@/shared/lib/gateway-protocol'

export class SpiritAgentGateway extends JsonRpcGatewayClient {
  readonly isProxy = false
}
