import { useStore } from '@nanostores/react'
import type React from 'react'

import { handleGatewayEvent } from '@/app/runtime/gateway-event-router'
import { useGatewayBoot } from '@/app/runtime/host-runtime'
import { useSurfaceSpriteLink } from '@/app/windows/sprite/use-surface-sprite-link'
import { $auth } from '@/shared/store/auth'

// 精灵窗入口装配：桌面走位与表面的联动 + 宿主 WS 生命周期。
// WS 只在鉴权后挂载——$auth 切回未鉴权（登出 / 过期）时组件卸载，
// useGatewayBoot 的 cleanup 负责拆掉连接；网关事件经 handleGatewayEvent 路由。
function GatewayBooter(): null {
  useGatewayBoot({ handleGatewayEvent })

  return null
}

export function SpriteBootstrap(): React.JSX.Element | null {
  useSurfaceSpriteLink()
  const auth = useStore($auth)

  return auth.kind === 'authenticated' ? <GatewayBooter /> : null
}
