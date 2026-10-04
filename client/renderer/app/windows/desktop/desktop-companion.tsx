import { SPRITE_SCALE_LIMITS } from '@ipc/contracts'
import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { CompanionEgg, useCompanionPresentation } from '@/app/components/companion-presentation'
import { SpriteStage } from '@/app/components/sprite-stage'
import { $defaultScale, hydrateActionCatalog, hydrateVideoPack, setDefaultScale } from '@/modules/character'
import { MediaStage } from '@/modules/character/rendering/video'
import { pushExternalAttachment } from '@/modules/conversation'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import { EyeOff } from '@/shared/lib/icons'

import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

export function DesktopCompanion({
  onHide,
  onOpenWhisper,
  onToggleWhisper,
  menuEnabled,
  onMenuOpenChange
}: {
  onHide: () => void
  onOpenWhisper: () => void
  onToggleWhisper: () => void
  menuEnabled: boolean
  onMenuOpenChange: (open: boolean) => void
}): React.JSX.Element {
  const presentation = useCompanionPresentation()
  const t = useDesktopStrings()
  const scale = useStore($defaultScale)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)

  useEffect(() => {
    void hydrateActionCatalog()
    void hydrateVideoPack()
  }, [])

  useEffect(() => {
    if (!menuEnabled) {
      setMenu(null)
    }
  }, [menuEnabled])

  useEffect(() => {
    onMenuOpenChange(menu !== null)

    return () => onMenuOpenChange(false)
  }, [menu, onMenuOpenChange])

  useEffect(() => {
    if (!menu || !menuEnabled) {
      return
    }

    const close = (): void => setMenu(null)

    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close()
      }
    }

    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [menu, menuEnabled])

  return (
    <div className={styles.companion}>
      <SpriteStage
        allowDisplaySwitch={false}
        domHitPassthrough
        onContextMenu={event => {
          if (!menuEnabled) {
            return
          }

          setMenu({
            x: Math.max(0, Math.min(event.clientX, window.innerWidth - 220)),
            y: Math.max(0, Math.min(event.clientY, window.innerHeight - 120))
          })
        }}
        onDoubleTap={onToggleWhisper}
        onDropFiles={files => {
          const paths = resolveDroppedFiles(files)

          if (paths.length) {
            pushExternalAttachment(paths)
            onOpenWhisper()
          }
        }}
        windowId={1}
      >
        {presentation.renderer === 'media' ? (
          <MediaStage />
        ) : (
          <CompanionEgg presentation={presentation} size="90%" windowId={1} />
        )}
      </SpriteStage>
      {menuEnabled && menu && (
        <div
          className={styles.companionMenu}
          onPointerDown={event => event.stopPropagation()}
          style={{ left: menu.x, top: menu.y }}
        >
          <label>
            {t.resizeCompanion}
            <input
              aria-label={t.resizeCompanion}
              max={SPRITE_SCALE_LIMITS.max}
              min={SPRITE_SCALE_LIMITS.min}
              onChange={event => setDefaultScale(Number(event.target.value))}
              step={0.05}
              type="range"
              value={scale}
            />
          </label>
          <button onClick={onHide} type="button">
            <EyeOff size={16} />
            {t.hideCompanion}
          </button>
        </div>
      )}
    </div>
  )
}
