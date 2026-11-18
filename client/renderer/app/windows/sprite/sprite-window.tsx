import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { ActivationOverlay, BootFailureOverlay, OnboardingFlow } from '@/app/onboarding'
import { useAccountLifecycle } from '@/app/workflows/account-lifecycle'
import { speakProactive } from '@/app/workflows/proactive-delivery'
import { useWindowPresentation } from '@/app/workflows/window-presentation'
import {
  $companionLifecycle,
  $companionVoiceId,
  ensureCompanionHydrated,
  hydratePortraitHistory,
  initSpatial,
  openContextMenu,
  reportUserActivity,
  resetToHomePosition,
  setCompanionLifecycle,
  setCompanionVoiceId,
  startActivityMonitor
} from '@/features/character'
import { SpriteStage } from '@/features/character'
import { CharacterStage } from '@/features/character/rendering'
import { enqueueGesture } from '@/features/creation/visuals'
import { MediaViewerOverlay } from '@/features/media'
import { checkVoiceValidity } from '@/features/speech'
import { useMainProcessListener } from '@/shared/hooks/use-main-process-listener'
import { useWindowMouseCapture } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { hydrateRunnerStatus } from '@/shared/store/runner-status'
import { $surfaceOpen, requestOpenSurface } from '@/shared/store/surfaces'
import { getStrings } from '@/shared/strings'
import { NotificationStack } from '@/ui'
import { ConfirmHost } from '@/ui'

import { SpriteContextMenu } from './context-menu'
import { DeveloperOverlay } from './developer-overlay'
import { ProactiveBubble } from './proactive-bubble'
import { toggleWhisper, WhisperOverlay } from './whisper'

function reportTap(): void {
  enqueueGesture('tap', speakProactive)
}

function reportDragReleased(): void {
  enqueueGesture('drag_released', speakProactive)
}

export function SpriteWindow(): React.JSX.Element {
  useWindowMouseCapture()
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const surfaceOpen = useStore($surfaceOpen)
  const lifecycle = useStore($companionLifecycle)
  const [activationOpen, setActivationOpen] = useState(false)

  const validityCheckedRef = useRef(false)

  useAccountLifecycle()
  useWindowPresentation()

  useEffect(() => {
    const stopSpatial = initSpatial()

    void hydrateRunnerStatus()

    return stopSpatial
  }, [])

  // 托盘唤起还须恢复 React 浮层状态。
  useMainProcessListener('onTrayActivate', () => setActivationOpen(true), [])

  useMainProcessListener('onTrayResetPosition', resetToHomePosition, [])

  useEffect(() => {
    if (auth.kind === 'unauthenticated') {
      setActivationOpen(true)
    } else if (auth.kind === 'authenticated') {
      setActivationOpen(false)
    }
  }, [auth.kind])

  const authed = auth.kind === 'authenticated'
  const showOnboarding = authed && lifecycle === 'onboarding'
  const showCompanion = authed && lifecycle === 'ready' && !activationOpen

  useEffect(() => {
    if (auth.kind !== 'authenticated' || lifecycle !== 'ready') {
      return
    }

    const onKey = () => reportUserActivity()
    window.addEventListener('keydown', onKey)

    const stopActivity = startActivityMonitor()

    void shellApi()
      .presentation.hostReady()
      .catch(error => log.warn('desktop', error))

    void ensureCompanionHydrated()
    void hydratePortraitHistory()

    return () => {
      window.removeEventListener('keydown', onKey)
      stopActivity()
    }
  }, [auth.kind, lifecycle])

  // 供应商目录变化会使已选音色失效，提示用户重新选择。
  useEffect(() => {
    if (lifecycle !== 'ready' || gatewayState !== 'open' || validityCheckedRef.current) {
      return
    }

    validityCheckedRef.current = true

    void checkVoiceValidity($companionVoiceId.get()).then(result => {
      if (result.valid) {
        return
      }

      setCompanionVoiceId('')

      const voice = getStrings().notifications.voice

      notify({
        kind: 'warning',
        title: voice.invalidTitle,
        message: voice.invalidMessage(result.name),
        action: {
          label: voice.invalidAction,
          onClick: () => {
            void requestOpenSurface({ location: { page: 'companion', section: 'voice' }, focus: { page: 'companion' } })
          }
        }
      })
    })
  }, [lifecycle, gatewayState])

  const onOnboardingComplete = (): void => {
    setCompanionLifecycle('ready')
  }

  return (
    <>
      {activationOpen && <ActivationOverlay onClose={() => setActivationOpen(false)} />}
      {showOnboarding && <OnboardingFlow onCompleted={onOnboardingComplete} />}
      <SpriteStage
        hidden={!showCompanion || surfaceOpen}
        onContextMenu={e => {
          openContextMenu({ x: e.clientX, y: e.clientY })
        }}
        onDoubleTap={toggleWhisper}
        onDragReleased={reportDragReleased}
        onTap={reportTap}
      >
        {showCompanion && <CharacterStage />}
      </SpriteStage>
      <SpriteContextMenu
        onOpenActivation={() => setActivationOpen(true)}
        onOpenSurface={() => {
          void requestOpenSurface()
        }}
      />
      <ProactiveBubble />
      <WhisperOverlay />
      {authed && <MediaViewerOverlay />}
      <NotificationStack />
      <ConfirmHost />
      <BootFailureOverlay />
      <DeveloperOverlay />
    </>
  )
}
