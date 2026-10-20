import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'

import {
  $companionLifecycle,
  ensureCompanionHydrated,
  hydrateActionCatalog,
  hydratePersona,
  hydratePortrait,
  hydrateVideoPack,
  SpriteVfxOverlay
} from '@/modules/character'
import { MediaStage, useMediaPixelHitTest } from '@/modules/character/rendering'
import { useInteractiveRegion } from '@/shared'
import { probeInteractiveRegions } from '@/shared/lib/interactive-regions'
import { cn } from '@/shared/lib/utils'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceCompanion } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import { CompanionEgg, useCompanionPresentation } from '../companion-presentation'

import styles from './surface-companion.module.css'

export function SurfaceCompanion(): React.JSX.Element | null {
  const state = useStore($surfaceCompanion)
  const mode = useStore($presentation)
  const hasSlot = state.slotWidth > 0
  const auth = useStore($auth)
  const lifecycle = useStore($companionLifecycle)

  const visible =
    state.visible && mode.effectiveMode === 'window' && auth.kind === 'authenticated' && lifecycle === 'ready'

  const strings = useStrings()
  const wrapperRef = useRef<HTMLDivElement>(null)
  const mediaHitTest = useMediaPixelHitTest(1)

  const presentation = useCompanionPresentation()

  const hitTest = useCallback(
    (x: number, y: number): boolean => visible && presentation.renderer === 'media' && mediaHitTest(x, y),
    [presentation.renderer, visible, mediaHitTest]
  )

  useInteractiveRegion('surface-companion', wrapperRef, undefined, hitTest, 1)

  useLayoutEffect(() => {
    probeInteractiveRegions(1)
  }, [state])

  useEffect(() => {
    const element = wrapperRef.current

    if (!element) {
      return
    }

    const observer = new ResizeObserver(() => probeInteractiveRegions(1))
    observer.observe(element)

    return () => observer.disconnect()
  }, [hasSlot])

  useEffect(() => {
    if (auth.kind !== 'authenticated' || lifecycle !== 'ready' || !visible) {
      return
    }

    void hydrateActionCatalog()
    void hydrateVideoPack()
    void ensureCompanionHydrated({ hydratePersona, hydratePortrait })
  }, [auth.kind, lifecycle, visible])

  if (!hasSlot) {
    return null
  }

  const slotWidth = state.outerWidth ? `${(state.slotWidth / state.outerWidth) * 100}%` : state.slotWidth

  return (
    <aside className={styles.slot} style={{ order: state.preference.side === 'left' ? -1 : 1, width: slotWidth }}>
      <div
        className={cn(styles.wrapper, presentation.renderer === 'media' && styles.videoWrapper)}
        onContextMenu={event => event.preventDefault()}
        ref={wrapperRef}
        title={strings.common.companionControl.dragTitle(strings.brand.name)}
      >
        <div className={styles.inner}>
          {visible &&
            (presentation.renderer === 'fallback' ? (
              <CompanionEgg presentation={presentation} size="min(280px, 92%)" windowId={1} />
            ) : (
              <MediaStage contentAlign={state.preference.side === 'right' ? 'left' : 'right'} />
            ))}
          {visible && <SpriteVfxOverlay />}
        </div>
      </div>
    </aside>
  )
}
