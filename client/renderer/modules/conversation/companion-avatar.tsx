import { useStore } from '@nanostores/react'
import type React from 'react'

import { cn } from '@/shared/lib/utils'
import { presentationPorts } from '@/shared/presentation-ports'

// 会话中的身份图共用肖像；已选头像但肖像未就绪时保留占位。
export function CompanionAvatar({ className }: { className?: string }): React.JSX.Element {
  const ports = presentationPorts()
  const portraitUrl = useStore(ports.$portraitUrl)
  const activeAvatarId = useStore(ports.$activeAvatarId)

  return (
    <div
      aria-hidden="true"
      className={cn(
        'mt-0.5 size-8 shrink-0 overflow-hidden rounded-full border border-line-standard bg-fill-faint shadow-sm',
        className
      )}
    >
      {portraitUrl ? (
        <img alt="" className="size-full object-cover" src={portraitUrl} />
      ) : activeAvatarId == null ? (
        <div className="flex size-full items-center justify-center bg-gradient-to-tr from-blue-600 to-indigo-500 text-[11px] font-bold text-white">
          S
        </div>
      ) : null}
    </div>
  )
}
