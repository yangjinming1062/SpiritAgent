import { useStore } from '@nanostores/react'
import React, { lazy, Suspense, useEffect, useRef, useState } from 'react'

import { ActivationOverlay, BootFailureOverlay, OnboardingFlow } from '@/app/onboarding'
import { useAccountLifecycle } from '@/app/workflows/account-lifecycle'
import { speakProactive } from '@/app/workflows/proactive-delivery'
import {
  $companionLifecycle,
  $companionVoiceId,
  $contextMenuPos,
  $videoPackStatus,
  EggStage,
  ensureCompanionHydrated,
  handlePokeInteraction,
  hydratePersona,
  hydratePortrait,
  hydratePortraitHistory,
  hydrateVideoPack,
  initSpatial,
  reportUserActivity,
  resetToHomePosition,
  resolveCompanionPresentation,
  setCompanionLifecycle,
  setCompanionVoiceId,
  startActivityMonitor
} from '@/modules/character'
import { MediaViewerOverlay } from '@/modules/media'
import { checkVoiceValidity, warmAudioContext } from '@/modules/speech'
import { NotificationStack, useGatewayRequest } from '@/shared'
import { useMainProcessListener } from '@/shared/hooks/use-main-process-listener'
import { useInteractiveRegion, useWindowMouseCapture } from '@/shared/lib/interactive-regions'
import { $auth, logout } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { hydrateRunnerStatus } from '@/shared/store/runner-status'
import { $surfaceOpen, requestOpenSurface, setSurfaceRole } from '@/shared/store/surfaces'
import { getStrings } from '@/shared/strings'

import { SpriteStage } from './behaviors/sprite-stage'
import { SpriteContextMenu } from './context-menu'
import { DeveloperOverlay } from './developer-overlay'
import { ProactiveBubble } from './proactive-bubble'
import { toggleWhisper, WhisperOverlay } from './whisper'

setSurfaceRole('sprite')

const VideoStage = lazy(() => import('@/modules/character/rendering/video').then(m => ({ default: m.VideoStage })))

export function SpriteWindow(): React.JSX.Element {
  useWindowMouseCapture()
  // toast 的关闭/展开按钮需要真实可点——透明窗口把它的矩形注册进交互区域。
  const notificationStackRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('notification-stack', notificationStackRef)
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const surfaceOpen = useStore($surfaceOpen)
  const lifecycle = useStore($companionLifecycle)
  const videoStatus = useStore($videoPackStatus)
  const [onboardingOpen, setOnboardingOpen] = useState(false)
  const [activationOpen, setActivationOpen] = useState(false)
  const hasHydratedRef = useRef(false)
  const { requestGateway } = useGatewayRequest()

  const validityCheckedRef = useRef(false)

  useAccountLifecycle()

  useEffect(() => initSpatial(), [])

  // 挂载时预热 AudioContext：已 onboarded 用户首次播音也会走冷启动 resume
  //（100–200ms），期间 MediaElementSource 重路由会丢掉首帧。预热把这段时间
  // 提前到用户抵达前。onboarding-flow 自己也调一次覆盖新用户路径。
  useEffect(() => {
    warmAudioContext()
  }, [])

  // 挂载时一次性水合 runner-status atom——与 hydrateAuth 同款模式，
  // 让伙伴侧消费者（activity.ts 等）能直接读 $runnerPhase，
  // 不必各自再实现 subscribe + 同步 getter 的组合。
  useEffect(() => {
    void hydrateRunnerStatus()
  }, [])

  // 托盘菜单的「登出」入口会触发这个桥；主进程侧登出也会在下一次会话检查时
  // 触发 `onSessionExpired`，但显式路由能让用户在点托盘项时 UI 更跟手。
  useMainProcessListener('onTrayLogout', () => void logout(), [])

  // 托盘「激活...」入口的对偶：主进程只调 showMainWindow() 不够——
  // 激活浮层是 React state，关掉之后必须显式翻回来，否则就是死锁。
  useMainProcessListener('onTrayActivate', () => setActivationOpen(true), [])

  // 托盘「一键归位」：将精灵落位与状态重置回默认 Home 位置
  useMainProcessListener(
    'onTrayResetPosition',
    () => {
      resetToHomePosition()
    },
    []
  )

  // 未鉴权时自动开激活浮层：首次 hydrateAuth 完成（pending → unauthenticated）、
  // 以及反激活之后。原先只能戳精灵触发，但未鉴权时精灵实体本身不可见、戳不到，
  // 这条链路在未激活用户那里是断的。
  useEffect(() => {
    if (auth.kind === 'unauthenticated') {
      setActivationOpen(true)
    }
  }, [auth.kind])

  // 仅开发期：注入一条测试主动消息（Ctrl+Shift+P）来跑通
  // companion.message 接收 + 气泡 + TTS 全链路，但不走 Backend 的 send_message 路径。
  // 生产构建里会被剔除。
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

  // 鉴权后查询 onboarding 状态。
  // 1) 先用 GET /api/companion/onboarding/state（REST）做权威即时检查。
  // 2) 不可用时回退到 requestGateway('onboarding.get_state')。
  // 3) 仅在 state?.complete === true 时把 lifecycle 设为 'ready'。
  //    不要回退到 persona.is_complete（角色题保存后它会在 onboarding 中途变成 true）。
  // 4) 若 state?.complete 不是 true，则 lifecycle='onboarding'，但 onboardingOpen 保持 false：
  //    让桌面蛋先常驻（DESIGN §4「蛋破碎后开始对话」），由用户戳击蛋才进入向导。
  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      setCompanionLifecycle('unauthed')
      setOnboardingOpen(false)

      return
    }

    let cancelled = false

    const checkState = async () => {
      let state: { complete?: boolean } | null = null

      try {
        state = await window.spiritagent.api<{ complete?: boolean }>({
          path: '/api/companion/onboarding/state'
        })
      } catch {
        state = await requestGateway<{ complete?: boolean }>('onboarding.get_state', {}).catch(() => null)
      }

      if (cancelled) {
        return
      }

      const onboardingDone = state?.complete === true
      setCompanionLifecycle(onboardingDone ? 'ready' : 'onboarding')
      // onboardingOpen 仅在用户主动戳击蛋 / 重新进入向导时才打开；
      // 保持 false 让 eggVisible 走通桌面蛋分支。
      setOnboardingOpen(false)

      if (onboardingDone) {
        void hydratePortrait()
        void hydratePortraitHistory()
      }
    }

    void checkState()

    return () => {
      cancelled = true
    }
  }, [auth.kind, requestGateway])

  const authed = auth.kind === 'authenticated'
  const showOnboarding = authed && lifecycle === 'onboarding' && onboardingOpen
  const eggVisible = authed && lifecycle === 'onboarding' && !onboardingOpen

  useEffect(() => {
    if (auth.kind !== 'authenticated' || lifecycle !== 'ready') {
      hasHydratedRef.current = false

      return
    }

    let cancelled = false
    const onKey = () => reportUserActivity()
    window.addEventListener('keydown', onKey)

    const stopActivity = startActivityMonitor()

    if (!hasHydratedRef.current) {
      hasHydratedRef.current = true

      void (async () => {
        if (cancelled || $auth.get().kind !== 'authenticated') {
          return
        }

        await ensureCompanionHydrated({
          hydratePersona,
          hydratePortrait
        })
        void hydrateVideoPack()
      })()
    }

    return () => {
      cancelled = true
      window.removeEventListener('keydown', onKey)
      stopActivity()
      // StrictMode dev double-invoke 兼容：cleanup 把 ref 复位，让 re-mount 重新水合。
      // 生产环境不会触发（无 cleanup → 无 re-mount），同 effect 不重复跑。
      hasHydratedRef.current = false
    }
  }, [auth.kind, lifecycle])

  // 检测云端目录里已经下架的伙伴 voice id（供应商裁剪 / 改名，或换了供应商）。
  // 后端对未知 id 是宽容的，这里只是一次性提示，不是硬错误。
  useEffect(() => {
    if (lifecycle !== 'ready' || gatewayState !== 'open' || validityCheckedRef.current) {
      return
    }

    validityCheckedRef.current = true

    void checkVoiceValidity($companionVoiceId.get(), requestGateway).then(result => {
      if (result.valid) {
        return
      }

      // 清除过期的 id，使下次 speak() 不再带 voice 参数（原 checkCompanionVoiceValidity 内部行为）。
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
  }, [lifecycle, gatewayState, requestGateway])

  // 视频就绪挂视频层，否则落程序化蛋兜底（DESIGN §1.2「永不空白」）。
  const presentation = React.useMemo(
    () => resolveCompanionPresentation({ videoReady: videoStatus === 'ready' }),
    [videoStatus]
  )

  const onTap = (): void => {
    if (authed) {
      if (lifecycle === 'onboarding') {
        setOnboardingOpen(true)

        return
      }

      // 单击触发戳击反应；双击精灵才打开轻语卡片。
      handlePokeInteraction()

      return
    }

    // 鉴权前：点击打开伙伴窗口内的激活浮层。
    setActivationOpen(true)
  }

  // 双击精灵：切换轻语卡片；未登录时打开激活浮层；onboarding 期间进引导。
  const onDoubleTap = (): void => {
    if (!authed) {
      setActivationOpen(true)

      return
    }

    if (lifecycle === 'onboarding') {
      setOnboardingOpen(true)

      return
    }

    toggleWhisper()
  }

  const onOnboardingComplete = (): void => {
    setOnboardingOpen(false)
    setCompanionLifecycle('ready')
  }

  return (
    <>
      {activationOpen && !authed && <ActivationOverlay onClose={() => setActivationOpen(false)} />}
      {showOnboarding && <OnboardingFlow onCompleted={onOnboardingComplete} />}
      <SpriteStage
        hidden={showOnboarding || surfaceOpen === 'living' || surfaceOpen === 'workbench'}
        onContextMenu={e => {
          $contextMenuPos.set({ x: e.clientX, y: e.clientY })
        }}
        onDoubleTap={onDoubleTap}
        onTap={onTap}
      >
        {eggVisible ? (
          <EggStage onTap={() => setOnboardingOpen(true)} />
        ) : showOnboarding ? null : (
          <Suspense fallback={null}>{presentation.renderer === 'video' ? <VideoStage /> : <EggStage />}</Suspense>
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
