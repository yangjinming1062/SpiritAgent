import { useStore } from '@nanostores/react'
import { IconRotateClockwise, IconVolume, IconVolumeOff } from '@tabler/icons-react'
import { useCallback, useEffect, useRef } from 'react'

import { usePresentationModeSwitch } from '@/app/components/presentation-mode-switch'
import { SPRITE_REGION_ID } from '@/app/components/sprite-stage'
import {
  $contextMenuPos,
  $quietUntil,
  $userPreferredTier,
  closeContextMenu,
  endQuiet,
  openContextMenu,
  QUIET_MINUTES,
  resetToHomePosition,
  setDefaultScale,
  setSpatialLocale,
  startQuiet
} from '@/modules/character'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { EyeOff, Home, type IconComponent, KeyRound, Monitor } from '@/shared/lib/icons'
import { isRegionHit, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { cn } from '@/shared/lib/utils'
import { SURFACE_OVERLAY } from '@/shared/panel/palette'
import { $auth } from '@/shared/store/auth'
import { requestCloseSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'
import type { SurfaceId } from '@ipc/contracts'

interface ContextMenuProps {
  onOpenActivation?: () => void
  onOpenSurface?: (surface: SurfaceId, view?: string) => void
}

const MENU_ITEM_CLASS =
  'flex h-8 w-full cursor-pointer items-center gap-2.5 rounded-xl px-2.5 text-left text-xs font-medium text-body transition-all duration-150 hover:bg-fill-hover hover:text-strong focus:bg-fill-hover focus:outline-none disabled:cursor-wait disabled:opacity-50'

function MenuItem({
  accent,
  icon: Icon,
  label,
  onClick,
  disabled = false
}: {
  accent?: boolean
  icon: IconComponent
  label: string
  onClick: () => void
  disabled?: boolean
}): React.JSX.Element {
  return (
    <button
      className={MENU_ITEM_CLASS}
      disabled={disabled}
      onClick={() => {
        onClick()
        closeContextMenu()
      }}
      type="button"
    >
      <Icon className={cn('size-4 shrink-0', accent ? 'text-accent' : 'text-muted')} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  )
}

function MenuDivider(): React.JSX.Element {
  return <div className="-mx-1.5 my-1 h-px bg-line-hairline opacity-60" />
}

// 剩余分钟向上取整，不足一分钟按 1 分钟显示。
function quietMinutesLeft(until: number): number {
  return Math.max(1, Math.ceil((until - Date.now()) / 60_000))
}

export function SpriteContextMenu({ onOpenActivation, onOpenSurface }: ContextMenuProps): React.JSX.Element {
  const { state: presentation, en, busy, switchMode } = usePresentationModeSwitch()
  const auth = useStore($auth)
  const pos = useStore($contextMenuPos)
  // 临时安静只设置或清除截止时间，不改写档位偏好；已选静止档时无需临时安静，不显示该项。
  const preferredTier = useStore($userPreferredTier)
  const quietUntil = useStore($quietUntil)
  const dict = useStrings()
  const visible = pos !== null
  const authed = auth.kind === 'authenticated'

  const backdropRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const handleRest = () => {
    void requestCloseSurface()
    resetToHomePosition()
    setDefaultScale(1)
    setSpatialLocale('home', { locomotion: 'fly' })
  }

  const handleHideSprite = () => {
    void window.spiritagent.sprite.hide()
  }

  const getInteractiveRect = useCallback(
    () => (visible && pos ? new DOMRect(0, 0, window.innerWidth, window.innerHeight) : null),
    [visible, pos]
  )

  useInteractiveRegion('sprite-context-menu', backdropRef, getInteractiveRect)

  useEscapeKey(closeContextMenu, { capture: false, enabled: visible, preventDefault: false, stopPropagation: false })

  useEffect(() => {
    if (!visible) {
      return
    }

    const handleBlur = () => {
      closeContextMenu()
    }

    window.addEventListener('blur', handleBlur)

    return () => window.removeEventListener('blur', handleBlur)
  }, [visible])

  const left = visible ? Math.min(pos.x, window.innerWidth - 200) : 0
  const top = visible ? Math.min(pos.y, window.innerHeight - 280) : 0

  return (
    <div
      className="fixed inset-0 z-50 select-none"
      onContextMenu={e => {
        e.preventDefault()
        e.stopPropagation()

        if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
          if (isRegionHit(SPRITE_REGION_ID, e.clientX, e.clientY)) {
            openContextMenu({ x: e.clientX, y: e.clientY })
          } else {
            closeContextMenu()
          }
        }
      }}
      onPointerDown={e => {
        if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
          e.preventDefault()
          e.stopPropagation()
          closeContextMenu()
        }
      }}
      ref={backdropRef}
      style={{
        pointerEvents: visible ? 'auto' : 'none',
        visibility: visible ? 'visible' : 'hidden'
      }}
    >
      <div
        className={`fixed z-50 min-w-44 origin-top-left overflow-hidden rounded-2xl p-1.5 text-xs text-strong select-none transition-all duration-150 ease-out ${SURFACE_OVERLAY}`}
        onPointerDown={e => {
          e.stopPropagation()
        }}
        ref={menuRef}
        style={{
          left,
          opacity: visible ? 1 : 0,
          pointerEvents: visible ? 'auto' : 'none',
          top,
          transform: visible ? 'scale(1)' : 'scale(0.96)'
        }}
      >
        {authed ? (
          <>
            <MenuItem accent icon={Home} label={dict.living.title} onClick={() => onOpenSurface?.('living')} />
            <MenuItem accent icon={Monitor} label={dict.workbench.title} onClick={() => onOpenSurface?.('workbench')} />
            {presentation.supported && presentation.effectiveMode !== 'desktop' && (
              <MenuItem
                disabled={busy}
                icon={Monitor}
                label={en ? 'Desktop mode' : '桌面模式'}
                onClick={() => void switchMode('desktop')}
              />
            )}
            <MenuDivider />
            {preferredTier !== 'still' ? (
              <MenuItem
                icon={quietUntil === null ? IconVolumeOff : IconVolume}
                label={
                  quietUntil === null
                    ? dict.companion.menu.quietOn(QUIET_MINUTES)
                    : dict.companion.menu.quietOff(quietMinutesLeft(quietUntil))
                }
                onClick={quietUntil === null ? startQuiet : endQuiet}
              />
            ) : null}
            <MenuItem icon={IconRotateClockwise} label={dict.companion.menu.resetPosition} onClick={handleRest} />
            <MenuDivider />
            <MenuItem icon={EyeOff} label={dict.companion.menu.hide} onClick={handleHideSprite} />
          </>
        ) : (
          <>
            <MenuItem icon={KeyRound} label={dict.companion.menu.activate} onClick={() => onOpenActivation?.()} />
            <MenuDivider />
            <MenuItem icon={EyeOff} label={dict.companion.menu.hide} onClick={handleHideSprite} />
          </>
        )}
      </div>
    </div>
  )
}
