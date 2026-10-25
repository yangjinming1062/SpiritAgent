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

  // 精灵窗默认鼠标穿透；全屏浮层按视口登记命中，避免逐帧测量布局。
  useInteractiveRegion('boot-failure', overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  if (!error) {
    return null
  }

  const message = dict.boot.desktopBootFailedWithMessage(error)

  const onRetry = () => {
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
