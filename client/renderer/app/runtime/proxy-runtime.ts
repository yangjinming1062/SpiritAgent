import { useEffect } from 'react'

import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { reportPrimaryGatewayState } from '@/shared/store/gateway'

import { handleGatewayEvent } from './gateway-event-router'

// 代理窗运行时：生活空间 / 工作台经主进程代理共享宿主 WS，本组件只做
// 代理事件泵与连接状态上报。tool.call 是宿主专属设备指令，代理窗口不接收——
// 宿主分发与重放去重在 handlers/tool-dispatch。
export function ProxyGatewayPump(): null {
  useEffect(() => {
    const desktop = window.spiritagent

    if (!desktop) {
      return
    }

    void desktop.gatewayGetState?.().then(st => {
      if (st) {
        reportPrimaryGatewayState(st)
      }
    })

    const offState = desktop.onGatewayStateChanged?.(payload => {
      if (payload?.state) {
        reportPrimaryGatewayState(payload.state)
      }
    })

    const offEvent = desktop.onGatewayEvent?.(payload => {
      if (payload?.event && payload.event.type !== 'tool.call') {
        handleGatewayEvent(payload.event as unknown as GatewayEvent)
      }
    })

    return () => {
      offState?.()
      offEvent?.()
    }
  }, [])

  return null
}
