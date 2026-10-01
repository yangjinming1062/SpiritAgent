import type React from 'react'
import { useEffect, useState } from 'react'

import { X } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

async function invokeSurface(action: 'close' | 'maximize' | 'minimize'): Promise<boolean> {
  try {
    await window.spiritagent?.surface?.[action]?.()

    return true
  } catch (err) {
    log.warn('window-controls', `${action} failed`, err)

    return false
  }
}

function ControlButton({
  children,
  danger = false,
  label,
  onClick
}: {
  children: React.ReactNode
  danger?: boolean
  label: string
  onClick: () => void
}): React.JSX.Element {
  return (
    <button
      aria-label={label}
      className={cn(
        'flex size-7 items-center justify-center rounded-lg text-muted transition-colors active:scale-95',
        danger ? 'hover:bg-rose-500/80 hover:text-white' : 'hover:bg-fill-hover hover:text-strong'
      )}
      onClick={onClick}
      title={label}
      type="button"
    >
      {children}
    </button>
  )
}

export function WindowControls(): React.JSX.Element {
  const { close, maximize, minimize, restore } = useStrings().common
  const [maximized, setMaximized] = useState(false)

  const checkMaximized = async (): Promise<void> => {
    try {
      const isMax = await window.spiritagent?.surface?.isMaximized?.()
      const flag = Boolean(isMax)
      setMaximized(flag)
      document.documentElement.dataset.maximized = flag ? 'true' : 'false'
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

  const handleMaximize = async (): Promise<void> => {
    if (!(await invokeSurface('maximize'))) {
      return
    }

    void checkMaximized()
    setTimeout(() => {
      void checkMaximized()
    }, 60)
  }

  return (
    <div className="flex items-center gap-0.5 [-webkit-app-region:no-drag]">
      <ControlButton label={minimize} onClick={() => void invokeSurface('minimize')}>
        <span className="h-[1.5px] w-2.5 rounded-full bg-current" />
      </ControlButton>

      <ControlButton label={maximized ? restore : maximize} onClick={() => void handleMaximize()}>
        {maximized ? (
          <div className="relative size-2.5">
            <span className="absolute -top-0.5 -right-0.5 size-2 rounded-[1.5px] border border-current opacity-70" />
            <span className="absolute bottom-0 left-0 size-2 rounded-[1.5px] border border-current bg-transparent" />
          </div>
        ) : (
          <span className="size-2.5 rounded-[1.5px] border border-current" />
        )}
      </ControlButton>

      <ControlButton danger label={close} onClick={() => void invokeSurface('close')}>
        <X className="size-3.5" />
      </ControlButton>
    </div>
  )
}
