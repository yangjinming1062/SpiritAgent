import { useStore } from '@nanostores/react'
import React, { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'

import { $mesh2dHitmap, $puppetReady, $renderMode, hydrateMesh2D, hydratePuppet } from '@/2d'
import { $glbLoadFailed, $modelInfo, hydrateModel } from '@/3d'
import { startActivityMonitor } from '@/companion/activity'
import { warmAudioContext } from '@/companion/audio-track'
import {
  $companionLifecycle,
  ensureCompanionHydrated,
  reportUserActivity,
  resolveCompanionRenderLayer,
  setCompanionLifecycle
} from '@/companion/companion-store'
import { hydratePersona } from '@/companion/persona-store'
import { hydratePortrait, hydratePortraitHistory } from '@/companion/portrait-store'
import { initSpatial, resetToHomePosition } from '@/companion/spatial'
import { useAuthBridge } from '@/companion/use-auth-bridge'
import { ActivationOverlay, BootFailureOverlay, EggStage, OnboardingFlow, useGatewayBoot } from '@/onboarding'
import { NotificationStack, useGatewayRequest } from '@/shared'
import { useMainProcessListener } from '@/shared/hooks/use-main-process-listener'
import { useInteractiveRegion, useWindowMouseCapture } from '@/shared/lib/interactive-regions'
import { $auth, logout } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { hydrateRunnerStatus } from '@/shared/store/runner-status'
import { $lastSurface, $surfaceOpen, requestOpenSurface, setSurfaceRole } from '@/shared/store/surfaces'
import { getStrings } from '@/shared/strings'

import { DeveloperOverlay } from './developer-overlay'
import { handleCompanionEvent } from './events'
import { handlePetInteraction, handlePokeInteraction, isPokeActive, normalizeRegion } from './interaction'
import { MediaViewerOverlay } from './media-viewer-overlay'
import { speakProactive } from './proactive/proactive'
import { ProactiveBubble } from './proactive/proactive-bubble'
import { SpriteContextMenu } from './sprite/context-menu'
import { $contextMenuPos } from './sprite/context-menu-store'
import { SpriteStage } from './sprite/sprite-stage'
import { checkCompanionVoiceValidity } from './voice-validity'

setSurfaceRole('sprite')

// 3D / 2D 渲染管线：把这两个组件连同其 three + draco wasm + GLTF loader 全家桶
// 从启动关键路径挪走。Onboarding 期间 (showOnboarding 为 true) 本来就不挂载它们，
// 让 Vite 把 three.module.js + draco_decoder.wasm 等 25MB 模块拆成单独 chunk，
// 在 lifecycle=ready 后按需请求，避开启动尖峰把风扇拉满。
const Companion3D = lazy(() => import('@/3d').then(m => ({ default: m.Companion3D })))
// puppet（PSD 链）沿用 WebGL/vite 懒加载策略：PSD 装配 + vendor
// rigger/ag-psd 都不在启动关键路径上（Phase 6）。
const PuppetStage = lazy(() => import('@/2d').then(m => ({ default: m.PuppetStage })))

// 把 gateway 启动挂在 mount effect 里——这样只在已鉴权时才会跑。
// 当 $auth 切回未鉴权（登出 / 过期）时这里会卸载，useGatewayBoot 的 cleanup
// 负责拆掉 WS。handleGatewayEvent 把流式聊天帧分派到 chat store + 状态机（events.ts）。
function GatewayBooter(): null {
  useGatewayBoot({
    handleGatewayEvent: handleCompanionEvent,
    onGatewayReady: () => {}
  })

  return null
}

export function CompanionRoot(): React.JSX.Element {
  useWindowMouseCapture()
  // toast 的关闭/展开按钮需要真实可点——透明窗口把它的矩形注册进交互区域。
  const notificationStackRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('notification-stack', notificationStackRef)
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const surfaceOpen = useStore($surfaceOpen)
  const lifecycle = useStore($companionLifecycle)
  const renderMode = useStore($renderMode)
  const puppetReady = useStore($puppetReady)
  const modelInfo = useStore($modelInfo)
  const glbLoadFailed = useStore($glbLoadFailed)
  const [onboardingOpen, setOnboardingOpen] = useState(false)
  const [activationOpen, setActivationOpen] = useState(false)
  const hasHydratedRef = useRef(false)
  const { requestGateway } = useGatewayRequest()

  const validityCheckedRef = useRef(false)

  useAuthBridge()

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

  // 渲染级联编排器（DESIGN §1.2「永不空白」）——嵌在组件体内，因依赖多个组件内 useStore 变量；
  // 抽到 useMemo 避免每次渲染重建闭包。2D = PSD 链（puppet，Phase 6）；puppet 装配失败
  // 写 error 熄灭 $puppetReady 后落 3D（CharacterController 内部有程序化蛋兜底）。
  const renderLayer = useMemo<'puppet' | 'companion3d'>(() => {
    return resolveCompanionRenderLayer({
      glbLoadFailed,
      modelStatus: modelInfo.status,
      puppetReady,
      renderMode
    })
  }, [renderMode, puppetReady, glbLoadFailed, modelInfo.status])

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
          hydrateMesh2D,
          hydrateModel,
          hydratePersona,
          hydratePortrait,
          hydratePuppet
        })
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

    void checkCompanionVoiceValidity(requestGateway).then(result => {
      if (result.valid) {
        return
      }

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

  const onTap = (nx?: number, ny?: number): void => {
    if (authed) {
      if (lifecycle === 'onboarding') {
        setOnboardingOpen(true)

        return
      }

      let rawRegion: string | undefined

      if (nx !== undefined && ny !== undefined) {
        const hit = $mesh2dHitmap.get()
        const result = hit ? hit.hit(nx, ny) : null
        rawRegion = result?.region
      }

      const region = normalizeRegion(rawRegion)

      // 单击 head 摸头（spec §4.3）
      if (region === 'head') {
        handlePetInteraction(nx, ny)

        return
      }

      // 处于 poke 激活窗口内时累加戳击
      if (isPokeActive()) {
        handlePokeInteraction(rawRegion)

        return
      }

      // 单击 body 切到生活空间（会话在生活空间里发生）
      void requestOpenSurface('living')

      return
    }

    // 鉴权前：点击打开伙伴窗口内的激活浮层。
    setActivationOpen(true)
  }

  // 双击精灵：开上次入口（plan §0）；未登录时打开激活浮层；onboarding 期间进引导。
  const onDoubleTap = (): void => {
    if (!authed) {
      setActivationOpen(true)

      return
    }

    if (lifecycle === 'onboarding') {
      setOnboardingOpen(true)

      return
    }

    void requestOpenSurface($lastSurface.get())
  }

  // onboarding 完成触发 3D 模型生成（base_texture 供应商是即时的——
  // 3D 生成触发在 confirm-front 成功回调里完成——onboarding 流程只负责"展示与完成"，不再触发 3D 任务。
  const onOnboardingComplete = (): void => {
    setOnboardingOpen(false)
    setCompanionLifecycle('ready')
  }

  return (
    <>
      {activationOpen && !authed && <ActivationOverlay onClose={() => setActivationOpen(false)} />}
      {showOnboarding && <OnboardingFlow onCompleted={onOnboardingComplete} />}
      <SpriteStage
        handlesEdgePose={renderLayer === 'puppet'}
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
          <Suspense fallback={null}>{renderLayer === 'puppet' ? <PuppetStage /> : <Companion3D />}</Suspense>
        )}
      </SpriteStage>
      <SpriteContextMenu
        onOpenActivation={() => setActivationOpen(true)}
        onOpenChat={() => {
          void requestOpenSurface('living')
        }}
        onOpenSurface={surface => {
          void requestOpenSurface(surface)
        }}
      />
      <ProactiveBubble />
      {authed && <MediaViewerOverlay />}
      <NotificationStack regionRef={notificationStackRef} />
      <BootFailureOverlay />
      <DeveloperOverlay />
      {authed && <GatewayBooter />}
    </>
  )
}
