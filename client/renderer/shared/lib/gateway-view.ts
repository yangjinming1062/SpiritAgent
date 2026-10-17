import { $gatewayState, type SpiritAgentGatewayLike } from '@/shared/store/gateway'

export function createGatewayView(isPresentationHost = false): SpiritAgentGatewayLike {
  return {
    isPresentationHost,
    get connectionState() {
      return $gatewayState.get()
    }
  }
}
