import {
  $effectiveTier,
  $spriteState,
  clearVfx,
  emitVfx,
  pushEffectiveDisturbanceTier,
  setSpriteState,
  startAutonomyProvision,
  stopAutonomyProvision
} from '@/modules/character'
import { refreshDesktopVideos } from '@/modules/desktop-videos'
import { cancelVoiceBar, stopSpeaking } from '@/modules/speech'
import { fetchSlashCommandMeta } from '@/shared/lib/slash-commands'
import { $presentation } from '@/shared/store/presentation'
import type { DesktopGatewayState } from '@ipc/contracts'

import { clearDesktopBootFailure, failDesktopBoot } from './boot-store'
import { startGatewayView } from './gateway-runtime'

/** 精灵窗口只管理呈现与会话视图，连接和本机派发由主进程持有。 */
export function startPresentationGateway(authSessionId: string): () => void {
  let graceTimer: ReturnType<typeof setTimeout> | undefined
  let open = false
  let observedState: DesktopGatewayState | undefined

  const syncAutonomy = (): void => {
    if (open && $presentation.get().stageOwner === 'sprite') {
      startAutonomyProvision()
    } else {
      stopAutonomyProvision()
    }
  }

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
    open = state === 'open'
    clearTimeout(graceTimer)
    syncAutonomy()

    if (open) {
      const tier = $effectiveTier.get()

      if (tier) {
        pushEffectiveDisturbanceTier(tier)
      }

      void refreshDesktopVideos()
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
        document.visibilityState === 'visible' ? 3000 : 30000
      )
    }
  })

  const offStage = $presentation.listen(syncAutonomy)

  return () => {
    clearTimeout(graceTimer)
    offStage()
    stopGateway?.()
    stopAutonomyProvision()
    clearDesktopBootFailure()
  }
}
