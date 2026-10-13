import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { $auth } from '@/shared/store/auth'

import { createProxyGatewayRuntime } from './gateway-proxy-runtime'

export function ProxyGatewayPump(): null {
  const auth = useStore($auth)
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null

  useEffect(() => {
    if (!sessionId) {
      return
    }

    const runtime = createProxyGatewayRuntime(sessionId)
    runtime.start()

    return runtime.dispose
  }, [sessionId])

  return null
}
