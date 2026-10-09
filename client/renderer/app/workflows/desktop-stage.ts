import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { applyStageActivity, initSpatial, setSpatialInsets, startAutonomyProvision } from '@/modules/character'
import { log } from '@/shared/lib/log'
import { $presentation } from '@/shared/store/presentation'

export function useDesktopStage({
  enabled,
  visible,
  insets
}: {
  enabled: boolean
  visible: boolean
  insets: { top: number; bottom: number; left: number; right: number }
}): void {
  const presentation = useStore($presentation)
  const { top, bottom, left, right } = insets

  useEffect(() => {
    setSpatialInsets({ top, bottom, left, right })

    return () => setSpatialInsets({ top: 0, bottom: 0, left: 0, right: 0 })
  }, [top, bottom, left, right])

  useEffect(() => {
    if (!enabled) {
      return
    }

    return initSpatial()
  }, [enabled])

  useEffect(() => {
    if (!enabled) {
      return
    }

    let disposed = false

    const apply = (activity: Parameters<typeof applyStageActivity>[0]): void => {
      if (!disposed) {
        applyStageActivity(activity)
      }
    }

    const stop = window.spiritagent.presentation.onStageActivity(apply)
    void window.spiritagent.presentation
      .getStageActivity()
      .then(apply)
      .catch(error => log.warn('desktop-stage', error))

    return () => {
      disposed = true
      stop()
    }
  }, [enabled])

  useEffect(() => {
    if (!enabled || !visible || !presentation.stageAvailable) {
      return
    }

    return startAutonomyProvision()
  }, [enabled, visible, presentation.stageAvailable, presentation.stageEpoch])
}
