import type React from 'react'
import { useEffect, useState } from 'react'

import { X } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'

export interface WindowControlsProps {
  className?: string
  onClose?: () => void
}

export function WindowControls({ className, onClose }: WindowControlsProps): React.JSX.Element {
  const [maximized, setMaximized] = useState(false)

  const checkMaximized = async (): Promise<void> => {
    try {
      const isMax = await window.spiritagent?.surface?.isMaximized?.()
      const flag = Boolean(isMax)
      setMaximized(flag)

      if (typeof document !== 'undefined') {
        document.documentElement.dataset.maximized = flag ? 'true' : 'false'
      }
    } catch (err) {
      log.warn('window-controls', 'checkMaximized failed', err)
    }
  }

  useEffect(() => {
    void checkMaximized()

    const onResize = (): void => {
      void checkMaximized()
    }

    window.addEventListener('resize', onResize)

    return () => window.removeEventListener('resize', onResize)
  }, [])

  const handleMinimize = async (): Promise<void> => {
    try {
      await window.spiritagent?.surface?.minimize?.()
    } catch (err) {
      log.warn('window-controls', 'minimize failed', err)
    }
  }

  const handleMaximize = async (): Promise<void> => {
    try {
      await window.spiritagent?.surface?.maximize?.()
      void checkMaximized()
      setTimeout(() => {
        void checkMaximized()
      }, 60)
    } catch (err) {
      log.warn('window-controls', 'maximize failed', err)
    }
  }

  const handleClose = async (): Promise<void> => {
    if (onClose) {
      onClose()

      return
    }

    try {
      await window.spiritagent?.surface?.close?.()
    } catch (err) {
      log.warn('window-controls', 'close failed', err)
    }
  }

  return (
    <div
      className={cn('flex items-center gap-0.5', className)}
      style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
    >
      <button
        aria-label="最小化"
        className="flex size-7 items-center justify-center rounded-lg text-muted transition-colors hover:bg-fill-hover hover:text-strong active:scale-95"
        onClick={() => void handleMinimize()}
        title="最小化"
        type="button"
      >
        <span className="h-[1.5px] w-2.5 rounded-full bg-current" />
      </button>

      <button
        aria-label={maximized ? '还原' : '最大化'}
        className="flex size-7 items-center justify-center rounded-lg text-muted transition-colors hover:bg-fill-hover hover:text-strong active:scale-95"
        onClick={() => void handleMaximize()}
        title={maximized ? '还原' : '最大化'}
        type="button"
      >
        {maximized ? (
          <div className="relative size-2.5">
            <span className="absolute -top-0.5 -right-0.5 size-2 rounded-[1.5px] border border-current opacity-70" />
            <span className="absolute bottom-0 left-0 size-2 rounded-[1.5px] border border-current bg-transparent" />
          </div>
        ) : (
          <span className="size-2.5 rounded-[1.5px] border border-current" />
        )}
      </button>

      <button
        aria-label="关闭"
        className="flex size-7 items-center justify-center rounded-lg text-muted transition-colors hover:bg-rose-500/80 hover:text-white active:scale-95"
        onClick={() => void handleClose()}
        title="关闭"
        type="button"
      >
        <X className="size-3.5" />
      </button>
    </div>
  )
}
