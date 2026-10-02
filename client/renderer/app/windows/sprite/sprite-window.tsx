import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { CompanionEgg, useCompanionPresentation } from '@/app/components/companion-presentation'
import { SpriteStage } from '@/app/components/sprite-stage'
import { ActivationOverlay, BootFailureOverlay, OnboardingFlow } from '@/app/onboarding'
import { useAccountLifecycle } from '@/app/workflows/account-lifecycle'
import { speakProactive } from '@/app/workflows/proactive-delivery'
import {
  $companionLifecycle,
  $companionVoiceId,
  ensureCompanionHydrated,
  hydrateActionCatalog,
  hydratePersona,
  hydratePortrait,
  hydratePortraitHistory,
  hydrateVideoPack,
  initSpatial,
  openContextMenu,
  reportUserActivity,
  resetToHomePosition,
  setCompanionLifecycle,
  setCompanionVoiceId,
  startActivityMonitor
} from '@/modules/character'
import { VideoStage } from '@/modules/character/rendering/video'
import { MediaViewerOverlay } from '@/modules/media'
import { checkVoiceValidity, warmAudioContext } from '@/modules/speech'
import { NotificationStack, requestGateway } from '@/shared'
import { useMainProcessListener } from '@/shared/hooks/use-main-process-listener'
import { useInteractiveRegion, useWindowMouseCapture } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
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
  // toast 的关闭/展开按钮需要真实可点——透明窗口把它的矩形注册进交互区域。
  const notificationStackRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('notification-stack', notificationStackRef)
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const surfaceOpen = useStore($surfaceOpen)
  const lifecycle = useStore($companionLifecycle)
  const presentation = useCompanionPresentation()
  const [activationOpen, setActivationOpen] = useState(false)

  const validityCheckedRef = useRef(false)

  useAccountLifecycle()

  useEffect(() => {
    const stopSpatial = initSpatial()

    // 挂载时预热 AudioContext：冷启动 resume 100–200ms 期间 MediaElementSource 重路由会丢首帧，预热把这段时间提前到用户抵达前；onboarding-flow 自己也调一次覆盖新用户路径。
    warmAudioContext()

    // 挂载时一次性水合 runner-status atom（与 hydrateAuth 同款），让伙伴侧消费者直接读 $runnerPhase，不必各自实现 subscribe+同步 getter。
    void hydrateRunnerStatus()

    return stopSpatial
  }, [])

  // 托盘「激活...」对偶：主进程只调 showMainWindow() 不够，激活浮层是 React state，关掉后必须显式翻回来否则死锁。
  useMainProcessListener('onTrayActivate', () => setActivationOpen(true), [])

  // 托盘「一键归位」：将精灵落位与状态重置回默认 Home 位置
  useMainProcessListener(
    'onTrayResetPosition',
    () => {
      resetToHomePosition()
    },
    []
  )

  // 未鉴权时自动开激活浮层：首次 hydrateAuth 完成（pending→unauthenticated）以及移除账户或会话失效后，保障未激活用户的激活入口可用。
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
  // 引导没有关闭入口（DESIGN「引导与后台准备」）：已激活且未完成时始终显示，完成后由 lifecycle 收起。
  const showOnboarding = authed && lifecycle === 'onboarding'

  // lifecycle 由 useAccountLifecycle 统一解析；本窗只据此决定向导，就绪后才启动活动监视并水合立绘与动作资产。
  useEffect(() => {
    if (auth.kind !== 'authenticated' || lifecycle !== 'ready') {
      return
    }

    const onKey = () => reportUserActivity()
    window.addEventListener('keydown', onKey)

    const stopActivity = startActivityMonitor()

    void window.spiritagent.presentation.hostReady().catch(error => log.warn('desktop', error))

    void hydrateActionCatalog()
    void hydrateVideoPack()
    void ensureCompanionHydrated({
      hydratePersona,
      hydratePortrait
    })
    void hydratePortraitHistory()

    return () => {
      window.removeEventListener('keydown', onKey)
      stopActivity()
    }
  }, [auth.kind, lifecycle])

  // 检测云端目录里已下架的伙伴 voice id（供应商裁剪/改名或换供应商）；后端对未知 id 宽容，这里只是一次性提示不是硬错误。
  useEffect(() => {
    if (lifecycle !== 'ready' || gatewayState !== 'open' || validityCheckedRef.current) {
      return
    }

    validityCheckedRef.current = true

    void checkVoiceValidity($companionVoiceId.get(), requestGateway).then(result => {
      if (result.valid) {
        return
      }

      // 清除过期的 id，使下次 speak() 不再带 voice 参数。
      setCompanionVoiceId('')

      const voice = getStrings().notifications.voice

      notify({
        kind: 'warning',
        title: voice.invalidTitle,
        message: voice.invalidMessage(result.name),
        action: {
          label: voice.invalidAction,
          onClick: () => {
            void requestOpenSurface('living', { view: 'settings/voice' })
          }
        }
      })
    })
  }, [lifecycle, gatewayState])

  // 鉴权前：点击打开伙伴窗口内的激活浮层。
  const onTap = (): void => {
    if (!authed) {
      setActivationOpen(true)
    }
  }

  // 双击精灵：切换轻语卡片；未登录时打开激活浮层。引导期间舞台已收起，不响应手势。
  const onDoubleTap = (): void => {
    if (!authed) {
      setActivationOpen(true)

      return
    }

    toggleWhisper()
  }

  const onOnboardingComplete = (): void => {
    setCompanionLifecycle('ready')
  }

  return (
    <>
      {activationOpen && <ActivationOverlay onClose={() => setActivationOpen(false)} />}
      {showOnboarding && <OnboardingFlow onCompleted={onOnboardingComplete} />}
      <SpriteStage
        hidden={showOnboarding || surfaceOpen === 'living' || surfaceOpen === 'workbench'}
        onContextMenu={e => {
          openContextMenu({ x: e.clientX, y: e.clientY })
        }}
        onDoubleTap={onDoubleTap}
        onTap={onTap}
      >
        {showOnboarding ? null : presentation.renderer === 'video' ? (
          <VideoStage />
        ) : (
          <CompanionEgg presentation={presentation} />
        )}
      </SpriteStage>
      <SpriteContextMenu
        onOpenActivation={() => setActivationOpen(true)}
        onOpenSurface={surface => {
          void requestOpenSurface(surface)
        }}
      />
      <ProactiveBubble />
      <WhisperOverlay />
      {authed && <MediaViewerOverlay />}
      <NotificationStack regionRef={notificationStackRef} />
      <BootFailureOverlay />
      <DeveloperOverlay />
    </>
  )
}
