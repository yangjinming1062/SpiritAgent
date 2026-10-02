import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import {
  applyStageActivity,
  cancelMovement,
  initSpatial,
  performRitualWalk,
  setSpatialInsets,
  startAutonomyProvision
} from '@/modules/character'
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
    void window.spiritagent.presentation.setStageVisible(visible).catch(error => log.warn('desktop-stage', error))
  }, [visible])

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
    if (!enabled || !visible || !presentation.foreground) {
      return
    }

    return startAutonomyProvision()
  }, [enabled, visible, presentation.foreground, presentation.stageEpoch])

  useEffect(() => {
    if (!enabled) {
      return
    }

    let disposed = false
    const controllers = new Map<string, AbortController>()

    const offCancellation = window.spiritagent.presentation.onRitualCancelled(reply => {
      if (reply.epoch !== presentation.stageEpoch) {
        return
      }

      const controller = controllers.get(reply.callId)

      if (controller && !controller.signal.aborted) {
        controller.abort()
        cancelMovement()
      }
    })

    const stop = window.spiritagent.presentation.onRitual(request => {
      const epoch = presentation.stageEpoch

      if (request.epoch !== epoch || !visible) {
        return
      }

      for (const previous of controllers.values()) {
        previous.abort()
      }

      cancelMovement()
      let prepared = false
      const controller = new AbortController()
      controllers.set(request.callId, controller)
      void performRitualWalk(
        () => Promise.resolve(request.rect),
        () =>
          Promise.resolve(
            prepared &&
              !controller.signal.aborted &&
              $presentation.get().foreground &&
              $presentation.get().stageEpoch === epoch
          ),
        {
          previewClick: false,
          signal: controller.signal,
          onPrepared: () => {
            prepared = true
          }
        }
      )
        .then(completed =>
          window.spiritagent.presentation.completeRitual({
            callId: request.callId,
            epoch,
            completed: !disposed && $presentation.get().stageEpoch === epoch && completed
          })
        )
        .catch(error => log.warn('desktop-stage', 'ritual failed', error))
        .finally(() => controllers.delete(request.callId))
    })

    return () => {
      disposed = true

      for (const controller of controllers.values()) {
        controller.abort()
      }

      offCancellation()
      stop()
    }
  }, [enabled, visible, presentation.stageEpoch])
}
