import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { gatewayApi } from '@/shared/platform/desktop'
import { $gateway } from '@/shared/store/gateway'

import { isRecord } from '../../../../shared/is-record'

export function useMemoryChanged(presetId: string, refresh: () => Promise<void>): void {
  const gateway = useStore($gateway)
  const beginGuard = useAsyncGuard()
  useEffect(() => {
    if (!gateway) {
      return
    }

    const isLive = beginGuard()

    const onEvent = (event: GatewayEvent): void => {
      if (
        isLive() &&
        event.type === 'memory.changed' &&
        isRecord(event.payload) &&
        event.payload.system_preset_id === presetId
      ) {
        void refresh()
      }
    }

    return gatewayApi().onGatewayEvent(({ event }) => onEvent(event))
  }, [beginGuard, gateway, presetId, refresh])
}
