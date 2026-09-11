import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef } from 'react'

import { $desktopBoot } from '@/app/runtime/boot-store'
import { useInteractiveRegion } from '@/modules/character'
import { BTN_PRIMARY, EmptyState } from '@/shared/panel'
import { setPrimaryGateway } from '@/shared/store/gateway'
import { useStrings } from '@/shared/strings'

export function BootFailureOverlay(): React.JSX.Element | null {
  const boot = useStore($desktopBoot)
  const dict = useStrings()
  const overlayRef = useRef<HTMLDivElement>(null)
  const isError = boot.phase === 'renderer.error' && boot.error

  // 把全出血浮层注册为可交互区域，让 Retry 按钮在默认鼠标穿透的精灵窗口里
  // 仍可点击——不注册的话，窗口的 setIgnoreMouseEvents(true, ...) 会在
  // 失败态下吞掉所有点击。rect 是编译期常量（position: fixed; inset: 0），
  // 因此跳过 getBoundingClientRect，直接返回视口——interactive-regions
  // 在失败态期间会针对屏幕上的每个 mousemove 调用此函数。
  useInteractiveRegion('boot-failure', overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  if (!isError) {
    return null
  }

  const message = boot.message || dict.boot.errors.desktopBootFailed

  const onRetry = () => {
    // Reload 会重跑启动流程，每次挂载只触发一次。
    setPrimaryGateway(null)
    window.location.reload()
  }

  return (
    <div
      aria-live="assertive"
      className="fixed inset-0 z-[1500] grid place-items-center bg-black/65 p-6 text-white backdrop-blur-lg"
      ref={overlayRef}
      role="alertdialog"
    >
      <div className="w-full max-w-md">
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
