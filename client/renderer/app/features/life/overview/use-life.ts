import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { $lifeRevision, refreshLife } from '@/modules/life'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'

export function useLife(visible = true): string {
  const revision = useStore($lifeRevision)
  const auth = useStore($auth)
  const gateway = useStore($gatewayState)

  useEffect(() => {
    if (visible && auth.kind === 'authenticated') {
      void refreshLife()
    }
  }, [visible, auth, gateway, revision])

  return `${auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind}:${gateway}:${revision}`
}
