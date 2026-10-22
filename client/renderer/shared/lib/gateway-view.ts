import { type SpiritAgentGatewayLike } from '@/shared/store/gateway'

export function createGatewayView(isPresentationHost = false): SpiritAgentGatewayLike {
  return { isPresentationHost }
}
