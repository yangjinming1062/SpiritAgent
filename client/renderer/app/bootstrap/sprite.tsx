import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect } from 'react'

import { startPresentationGateway } from '@/app/runtime/gateway-host-runtime'
import { useSurfaceSpriteLink } from '@/app/windows/sprite/use-surface-sprite-link'
import { useDesktopCompanionActivityPublisher } from '@/app/workflows/desktop-companion-activity'
import { $auth } from '@/shared/store/auth'

// 精灵窗装配桌面走位、表面联动和账户连接视图；网络与本机工具生命周期归主进程。
function GatewayBooter({ sessionId }: { sessionId: string }): null {
  useEffect(() => startPresentationGateway(sessionId), [sessionId])

  return null
}

export function SpriteBootstrap(): React.JSX.Element | null {
  useDesktopCompanionActivityPublisher()
  useSurfaceSpriteLink()
  const auth = useStore($auth)

  return auth.kind === 'authenticated' ? (
    <GatewayBooter key={auth.snapshot.sessionId} sessionId={auth.snapshot.sessionId} />
  ) : null
}
