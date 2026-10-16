import { useEffect } from 'react'

import { startPresentationGateway } from './gateway-host-runtime'

export function useGatewayBoot(sessionId: string): void {
  useEffect(() => startPresentationGateway(sessionId), [sessionId])
}
