import { useStore } from '@nanostores/react'
import type React from 'react'

import { presentationPorts } from '@/shared/presentation-ports'

// 工作台消息流里的伙伴头像：有肖像用肖像；尚未选定头像时回退首字母；已选头像但肖像未就绪时留空。
export function CompanionAvatar(): React.JSX.Element {
  const ports = presentationPorts()
  const portraitUrl = useStore(ports.$portraitUrl)
  const activeAvatarId = useStore(ports.$activeAvatarId)

  return (
    <div className="mt-0.5 size-8 shrink-0 overflow-hidden rounded-full border border-white/15 bg-white/10 shadow-sm">
      {portraitUrl ? (
        <img alt="Companion" className="size-full object-cover" src={portraitUrl} />
      ) : activeAvatarId == null ? (
        <div className="flex size-full items-center justify-center bg-gradient-to-tr from-blue-600 to-indigo-500 text-[11px] font-bold text-white">
          S
        </div>
      ) : null}
    </div>
  )
}
