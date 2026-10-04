import { type DesktopCompanionInteraction, SPRITE_SCALE_LIMITS } from '@ipc/contracts'
import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef } from 'react'

import { CompanionEgg, useCompanionPresentation } from '@/app/components/companion-presentation'
import { SpriteStage } from '@/app/components/sprite-stage'
import { useDesktopCompanionActivityMirror } from '@/app/workflows/desktop-companion-activity'
import { useDesktopStage } from '@/app/workflows/desktop-stage'
import {
  $defaultScale,
  ensureCompanionHydrated,
  hydrateActionCatalog,
  hydratePersona,
  hydratePortrait,
  hydrateVideoPack,
  setDefaultScale
} from '@/modules/character'
import { MediaStage } from '@/modules/character/rendering/video'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import { EyeOff } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceScreenLocked } from '@/shared/store/surfaces'

import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

export function DesktopCompanion(): React.JSX.Element | null {
  useDesktopCompanionActivityMirror()
  const character = useCompanionPresentation()
  const presentation = useStore($presentation)
  const auth = useStore($auth)
  const locked = useStore($surfaceScreenLocked)
  const visible = presentation.status === 'active' && presentation.stageVisible && !presentation.fullscreen && !locked
  useDesktopStage({ enabled: auth.kind === 'authenticated', visible, insets: presentation.stageInsets })

  useEffect(() => {
    void hydrateActionCatalog()
    void hydrateVideoPack()
    void ensureCompanionHydrated({ hydratePersona, hydratePortrait })
  }, [])

  const interact = (interaction: DesktopCompanionInteraction): void => {
    void window.spiritagent.presentation
      .companionInteraction(interaction)
      .catch(error => log.warn('desktop-companion', error))
  }

  if (!visible) {
    return null
  }

  return (
    <div className={styles.companion}>
      <SpriteStage
        allowDisplaySwitch={false}
        onContextMenu={event => interact({ kind: 'menu', x: event.clientX, y: event.clientY })}
        onDoubleTap={() => interact({ kind: 'toggle-whisper' })}
        onDropFiles={files => {
          const paths = resolveDroppedFiles(files)

          if (paths.length) {
            interact({ kind: 'drop', paths })
          }
        }}
        windowId={1}
      >
        {character.renderer === 'media' ? (
          <MediaStage />
        ) : (
          <CompanionEgg presentation={character} size="90%" windowId={1} />
        )}
      </SpriteStage>
    </div>
  )
}

export function DesktopCompanionMenu({
  position,
  onClose,
  onHide
}: {
  position: { x: number; y: number }
  onClose: () => void
  onHide: () => void
}): React.JSX.Element {
  const t = useDesktopStrings()
  const scale = useStore($defaultScale)
  const ref = useRef<HTMLDivElement>(null)
  useInteractiveRegion('desktop-companion-menu', ref)
  useEffect(() => {
    const pointer = (event: PointerEvent): void => {
      if (event.target instanceof Node && !ref.current?.contains(event.target)) {
        onClose()
      }
    }

    const key = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
    }

    window.addEventListener('pointerdown', pointer)
    window.addEventListener('keydown', key)

    return () => {
      window.removeEventListener('pointerdown', pointer)
      window.removeEventListener('keydown', key)
    }
  }, [onClose])

  return (
    <div
      className={styles.companionMenu}
      ref={ref}
      style={{
        left: Math.max(0, Math.min(position.x, window.innerWidth - 220)),
        top: Math.max(0, Math.min(position.y, window.innerHeight - 120))
      }}
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
      <button
        onClick={() => {
          onHide()
          onClose()
        }}
        type="button"
      >
        <EyeOff size={16} />
        {t.hideCompanion}
      </button>
    </div>
  )
}
