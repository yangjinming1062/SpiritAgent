import type { SurfaceId } from '@ipc/contracts'
import { useStore } from '@nanostores/react'
import { IconRotateClockwise, IconVolume, IconVolumeOff } from '@tabler/icons-react'
import { useCallback, useEffect, useRef } from 'react'

import {
  $contextMenuPos,
  $effectiveTier,
  $userPreferredTier,
  closeContextMenu,
  type DisturbanceTier,
  resetToHomePosition,
  setDefaultScale,
  setDisturbanceTier,
  setSpatialLocale
} from '@/modules/character'
import { EyeOff, Home, type IconComponent, KeyRound, Monitor } from '@/shared/lib/icons'
import { isRegionHit, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { SURFACE_OVERLAY } from '@/shared/panel/palette'
import { $auth } from '@/shared/store/auth'
import { requestCloseSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

interface ContextMenuProps {
  onOpenActivation?: () => void
  onOpenSurface?: (surface: SurfaceId, view?: string) => void
}

const MENU_ITEM_CLASS =
  'flex h-8 w-full cursor-pointer items-center gap-2.5 rounded-xl px-2.5 text-left text-xs font-medium text-body transition-all duration-150 hover:bg-fill-hover hover:text-strong focus:bg-fill-hover focus:outline-none'

function MenuItem({
  accent,
  icon: Icon,
  label,
  onClick
}: {
  accent?: boolean
  icon: IconComponent
  label: string
  onClick: () => void
}): React.JSX.Element {
  return (
    <button
      className={MENU_ITEM_CLASS}
      onClick={() => {
        onClick()
        closeContextMenu()
      }}
      type="button"
    >
      <Icon className={`size-4 shrink-0 ${accent ? 'text-accent' : 'text-muted'}`} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  )
}

function MenuDivider(): React.JSX.Element {
  return <div className="-mx-1.5 my-1 h-px bg-line-hairline opacity-60" />
}

// 精灵右键快捷菜单（超高质感液态玻璃）：收敛为生活空间与工作台两大入口
export function SpriteContextMenu({ onOpenActivation, onOpenSurface }: ContextMenuProps): React.JSX.Element {
  const auth = useStore($auth)
  const pos = useStore($contextMenuPos)
  // 按用户偏好判断安静：全屏 / 游戏的临时静止不算用户选择，不能被开关覆盖成常规。
  const preferredTier = useStore($userPreferredTier)
  const dict = useStrings()
  const visible = pos !== null
  const authed = auth.kind === 'authenticated'
  const isStill = preferredTier === 'still'

  const backdropRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const quietTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 退出安静时恢复进入前的档位。
  const restoreTierRef = useRef<DisturbanceTier>('normal')

  useEffect(() => {
    if (preferredTier !== 'still') {
      restoreTierRef.current = preferredTier
    }
  }, [preferredTier])

  useEffect(() => {
    return () => {
      if (quietTimerRef.current) {
        clearTimeout(quietTimerRef.current)
      }
    }
  }, [])

  const toggleQuiet = () => {
    if (quietTimerRef.current) {
      clearTimeout(quietTimerRef.current)
      quietTimerRef.current = null
    }

    if (isStill) {
      setDisturbanceTier(restoreTierRef.current)
    } else {
      setDisturbanceTier('still')
      // 50 分钟后自动恢复
      quietTimerRef.current = setTimeout(
        () => {
          quietTimerRef.current = null

          if ($effectiveTier.get() === 'still') {
            setDisturbanceTier('normal')
          }
        },
        50 * 60 * 1000
      )
    }
  }

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

  useEffect(() => {
    if (!visible) {
      return
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        closeContextMenu()
      }
    }

    const handleBlur = () => {
      closeContextMenu()
    }

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('blur', handleBlur)

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('blur', handleBlur)
    }
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
          if (isRegionHit('sprite-stage', e.clientX, e.clientY)) {
            $contextMenuPos.set({ x: e.clientX, y: e.clientY })
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
            <MenuDivider />
            <MenuItem
              icon={isStill ? IconVolume : IconVolumeOff}
              label={isStill ? dict.companion.menu.quietOff : dict.companion.menu.quietOn}
              onClick={toggleQuiet}
            />
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
