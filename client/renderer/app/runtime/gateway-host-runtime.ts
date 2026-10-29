import {
  $effectiveTier,
  $spriteState,
  clearVfx,
  emitVfx,
  pushEffectiveDisturbanceTier,
  setSpriteState
} from '@/modules/character'
import { cancelVoiceBar, stopSpeaking } from '@/modules/speech'
import { refreshVisuals } from '@/modules/visuals'
import { RENDERER_TIMINGS } from '@/shared/constants'
import { fetchSlashCommandMeta } from '@/shared/lib/slash-commands'
import type { DesktopGatewayState } from '@ipc/contracts'

import { clearDesktopBootFailure, failDesktopBoot } from './boot-store'
import { startGatewayView } from './gateway-runtime'

/** 精灵窗口只管理呈现与会话视图，连接和本机派发由主进程持有。 */
export function startPresentationGateway(authSessionId: string): () => void {
  let graceTimer: ReturnType<typeof setTimeout> | undefined
  let observedState: DesktopGatewayState | undefined

  const stopGateway = startGatewayView(authSessionId, true, snapshot => {
    const { state, error, hasConnected } = snapshot

    if (!hasConnected && error) {
      failDesktopBoot(error)
    } else {
      clearDesktopBootFailure()
    }

    if (state === observedState) {
      return
    }

    observedState = state
    clearTimeout(graceTimer)

    if (state === 'open') {
      const tier = $effectiveTier.get()

      if (tier) {
        pushEffectiveDisturbanceTier(tier)
      }

      void refreshVisuals()
      void fetchSlashCommandMeta()
      clearVfx('sleep_zzz')

      if ($spriteState.get() === 'disconnected') {
        setSpriteState('idle', { force: true })
      }
    } else if (state === 'closed' || state === 'error') {
      cancelVoiceBar()
      stopSpeaking()
      graceTimer = setTimeout(
        () => {
          setSpriteState('disconnected')
          emitVfx('sleep_zzz', { nx: 0.5, ny: 0.05 })
        },
        document.visibilityState === 'visible'
          ? RENDERER_TIMINGS.desktopDisconnectedGraceVisibleMs
          : RENDERER_TIMINGS.desktopDisconnectedGraceHiddenMs
      )
    }
  })

  return () => {
    clearTimeout(graceTimer)
    stopGateway?.()
    clearDesktopBootFailure()
  }
}
