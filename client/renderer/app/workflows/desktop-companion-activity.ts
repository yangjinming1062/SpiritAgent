import type { DesktopCompanionActivityState } from '@ipc/contracts'
import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { $spriteState, setDesktopCompanionActivity } from '@/modules/character'
import { $voiceBarPlayingId } from '@/modules/conversation'
import { $voicePreparing, setDesktopVoicePreparing } from '@/modules/speech'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceRole } from '@/shared/store/surfaces'

export function useDesktopCompanionActivityPublisher(): void {
  const auth = useStore($auth)
  const sessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null

  useEffect(() => {
    if (!sessionId || !['sprite', 'desktop'].includes($surfaceRole.get() ?? '')) {
      return
    }

    let published: { state: DesktopCompanionActivityState; voicePreparing: boolean } | undefined

    const publish = (): void => {
      const current = $auth.get()

      if (current.kind !== 'authenticated' || current.snapshot.sessionId !== sessionId) {
        return
      }

      const state =
        $gatewayState.get() !== 'open' ? 'disconnected' : $voiceBarPlayingId.get() ? 'speaking' : $spriteState.get()

      if (state === 'emotional' || state === 'interacting') {
        return
      }

      const activity = { state, voicePreparing: $voicePreparing.get() }

      if (published?.state === activity.state && published.voicePreparing === activity.voicePreparing) {
        return
      }

      published = activity
      void window.spiritagent.presentation.companionActivity({ ...activity, authSessionId: sessionId }).catch(error => {
        if (published === activity) {
          published = undefined
        }

        log.warn('desktop-companion', 'activity publish failed', error)
      })
    }

    const stops = [
      $spriteState.listen(publish),
      $voiceBarPlayingId.listen(publish),
      $voicePreparing.listen(publish),
      $gatewayState.listen(publish)
    ]

    publish()

    return () => stops.forEach(stop => stop())
  }, [sessionId])
}

export function useDesktopCompanionActivityMirror(): void {
  const presentation = useStore($presentation)
  useEffect(() => {
    const state: DesktopCompanionActivityState =
      presentation.effectiveMode === 'desktop' ? presentation.companionActivity : 'idle'

    setDesktopCompanionActivity(state)
    setDesktopVoicePreparing(presentation.effectiveMode === 'desktop' && presentation.voicePreparing)
  }, [presentation.companionActivity, presentation.effectiveMode, presentation.voicePreparing])
  useEffect(
    () => () => {
      setDesktopCompanionActivity('idle')
      setDesktopVoicePreparing(false)
    },
    []
  )
}
