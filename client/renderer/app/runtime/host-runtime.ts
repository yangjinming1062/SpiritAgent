import { useEffect } from 'react'

import { createGatewayHostRuntime } from './gateway-host-runtime'

export function useGatewayBoot(sessionId: string): void {
  useEffect(() => {
    const runtime = createGatewayHostRuntime()
    runtime.start()

    return () => runtime.dispose()
  }, [sessionId])
}
