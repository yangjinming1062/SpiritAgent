import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef } from 'react'

import { $desktopBootError } from '@/app/runtime/boot-store'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { BTN_PRIMARY, EmptyState, SURFACE_OVERLAY } from '@/shared/panel'
import { setPrimaryGateway } from '@/shared/store/gateway'
import { useStrings } from '@/shared/strings'

export function BootFailureOverlay(): React.JSX.Element | null {
  const error = useStore($desktopBootError)
  const dict = useStrings()
  const overlayRef = useRef<HTMLDivElement>(null)

  // 把全出血浮层注册为可交互区域，让 Retry 按钮在默认鼠标穿透的精灵窗口里仍可点击（不注册则 setIgnoreMouseEvents 会吞掉所有点击）。rect 是编译期常量（fixed inset:0），直接返回视口——失败态期间每个 mousemove 都会调此函数。
  useInteractiveRegion('boot-failure', overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  if (!error) {
    return null
  }

  const message = dict.boot.desktopBootFailedWithMessage(error)

  const onRetry = () => {
    // Reload 会重跑启动流程，每次挂载只触发一次。
    setPrimaryGateway(null)
    window.location.reload()
  }

  return (
    <div
      aria-live="assertive"
      className="fixed inset-0 z-[1500] grid place-items-center p-6"
      ref={overlayRef}
      role="alertdialog"
    >
      <div className={`w-full max-w-md rounded-2xl p-5 text-strong ${SURFACE_OVERLAY}`}>
        <EmptyState
          action={
            <button className={BTN_PRIMARY} onClick={onRetry} type="button">
              {dict.boot.failure.retry}
            </button>
          }
          description={message}
          title={dict.boot.errors.desktopBootFailed}
        />
      </div>
    </div>
  )
}
