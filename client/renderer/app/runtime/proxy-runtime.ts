import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { $auth } from '@/shared/store/auth'

import { startGatewayView } from './gateway-runtime'

export function ProxyGatewayPump(): null {
  const auth = useStore($auth)
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null

  useEffect(() => {
    if (!sessionId) {
      return
    }

    return startGatewayView(sessionId)
  }, [sessionId])

  return null
}
