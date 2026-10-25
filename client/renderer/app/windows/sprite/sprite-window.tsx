import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { SpriteStage } from '@/app/components/sprite-stage'
import { ActivationOverlay, BootFailureOverlay, OnboardingFlow } from '@/app/onboarding'
import { useAccountLifecycle } from '@/app/workflows/account-lifecycle'
import { speakProactive } from '@/app/workflows/proactive-delivery'
import { useVisualProjection } from '@/app/workflows/visual-presentation'
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
} from '@/modules/character'
import { OutfitStage } from '@/modules/character/rendering'
import { MediaViewerOverlay } from '@/modules/media'
import { checkVoiceValidity, warmAudioContext } from '@/modules/speech'
import { NotificationStack } from '@/shared'
import { useMainProcessListener } from '@/shared/hooks/use-main-process-listener'
import { useWindowMouseCapture } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
import { ConfirmHost } from '@/shared/panel'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { hydrateRunnerStatus } from '@/shared/store/runner-status'
import { $surfaceOpen, requestOpenSurface } from '@/shared/store/surfaces'
import { getStrings } from '@/shared/strings'

import { SpriteContextMenu } from './context-menu'
import { DeveloperOverlay } from './developer-overlay'
import { ProactiveBubble } from './proactive-bubble'
import { toggleWhisper, WhisperOverlay } from './whisper'

export function SpriteWindow(): React.JSX.Element {
  useWindowMouseCapture()
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const surfaceOpen = useStore($surfaceOpen)
  const lifecycle = useStore($companionLifecycle)
  const [activationOpen, setActivationOpen] = useState(false)

  const validityCheckedRef = useRef(false)

  useAccountLifecycle()
  useVisualProjection()

  useEffect(() => {
    const stopSpatial = initSpatial()

    // 提前预热，避免首次播放时 resume 与 MediaElementSource 重路由叠加丢首帧。
    warmAudioContext()

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

  // 仅开发期：Ctrl+Shift+P 直接调 speakProactive 验证主动气泡与朗读，不经网关 companion.message；生产构建剔除。
  useEffect(() => {
    if (import.meta.env.PROD) {
      return
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault()
        void speakProactive('（测试）嘿，休息一下眼睛吧～')
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [])

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
            void requestOpenSurface({ page: 'companion', companionSection: 'voice' })
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
      >
        {showCompanion && <OutfitStage />}
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
