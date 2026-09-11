import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import {
  $clipOverride,
  $contextMenuOpen,
  $dragVelocity,
  $gazeTarget,
  $spatialLocomotion,
  $spriteAction,
  $spriteContentRect,
  $spriteEmotion,
  $spriteState,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  probeInteractiveRegions,
  registerAmplitudeSink,
  type SpriteEmotion,
  type SpriteStateName,
  useInteractiveRegion
} from '@/companion'
import { log } from '@/shared/lib/log'
import { $surfaceOpen } from '@/shared/store/surfaces'

import { Engine } from './Engine'
import { fetchGlbWithCache } from './glb-opfs-cache'
import {
  $clipMap,
  $glbLoadFailed,
  $modelGenError,
  $modelGenProgress,
  $modelGenState,
  $modelInfo,
  $modelLoadSettled,
  $modelRetryable,
  $modelRetryModelId,
  retryModelDownload
} from './model-store'
import { subscribePowerProfile } from './power-signals'
import { alphaMapContentRect, attachSilhouetteHitProbe } from './silhouette-hit'

// 把 Three.js 引擎挂载到精灵舞台画布上。精灵舞台负责拖拽 / 鼠标穿透 / 区域跟踪；
// 本组件只负责渲染。
//
// - Engine.create 是异步的（WebGPU 初始化）—— ready promise 与下面的 load/outfit 副作用共享，
//   永远不会和启动竞速；返回 null 表示组件在初始化过程中被卸载。
// - 订阅 $spriteState / $spriteEmotion 并转发给角色控制器做动画 + 变形播放。
//   快照在引擎就绪后（await 之后）才取，因此异步启动窗口内的状态翻转不会丢失。
// - 响应 $modelInfo.asset_url 变化来重新加载 GLB。
// - TTS 唇形同步：通过 tts-bridge 钩到 audio-track 的 AnalyserNode。
// - 视线跟随：在聊天面板关闭时跟踪画布上的光标。

interface CharacterSnapshot {
  state: SpriteStateName
  emotion: SpriteEmotion | null
  action: string | null
}

function captureSpriteSnapshot(): CharacterSnapshot {
  return { state: $spriteState.get(), emotion: $spriteEmotion.get(), action: $spriteAction.get() }
}

export function Companion3D(): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const engineReadyRef = useRef<Promise<Engine | null> | null>(null)
  const loadedIdentityRef = useRef<string | null>(null)
  const modelInfo = useStore($modelInfo)

  // 启动引擎，接好订阅，并触发首次模型加载。
  useEffect(() => {
    const container = containerRef.current

    if (!container) {
      return
    }

    let cancelled = false
    let engine: Engine | null = null
    let detachWiring: (() => void) | null = null

    const ready = Engine.create(container).then(created => {
      if (cancelled) {
        created.dispose()

        return null
      }

      const eng = created
      engine = created

      const initial = captureSpriteSnapshot()
      eng.character.setClipMap($clipMap.get())
      eng.character.applyState(initial.state, initial.emotion, { action: initial.action })

      const unsubState = $spriteState.listen(state => {
        const override = state === 'interacting' ? $clipOverride.get() : undefined
        eng.character.applyState(state, $spriteEmotion.get(), {
          clipOverride: override,
          action: $spriteAction.get()
        })

        if (state !== 'interacting') {
          $clipOverride.set(null)
        }
      })

      const unsubEmotion = $spriteEmotion.listen(emotion => {
        eng.character.applyState($spriteState.get(), emotion, { action: $spriteAction.get() })
      })

      const unsubGenerated = $clipMap.listen(clipMap => eng.character.setClipMap(clipMap))

      // TTS 唇形同步 —— audio-track 的 AnalyserNode 在音频播放期间逐帧推振幅；
      // 我们只做转发。
      const detachLipSync = registerAmplitudeSink(amp => eng.character.setLipSyncAmplitude(amp))

      let cachedW = getBaseSpriteWidth()
      let cachedH = getBaseSpriteHeight()

      const onResize = () => {
        const container = containerRef.current
        cachedW = container?.clientWidth || eng.canvas.clientWidth || getBaseSpriteWidth()
        cachedH = container?.clientHeight || eng.canvas.clientHeight || getBaseSpriteHeight()
        eng.resize(cachedW, cachedH)
      }

      const ro = new ResizeObserver(onResize)

      if (containerRef.current) {
        ro.observe(containerRef.current)
      } else {
        ro.observe(eng.canvas)
      }

      window.addEventListener('resize', onResize)

      // 视线跟随 —— 在伙伴上悬停时平滑跟随光标，避免触发 DOM 回流；
      // 显式视线目标（ritual walk 途中锁定目标窗口）优先于指针。
      const onPointerMove = (e: PointerEvent) => {
        if ($gazeTarget.get()) {
          return
        }

        if ($surfaceOpen.get() !== null || $spatialLocomotion.get() === 'drag' || $contextMenuOpen.get()) {
          eng.character.setLookTarget(0, 0)

          return
        }

        const nx = (e.offsetX / (cachedW || 1)) * 2 - 1
        const ny = (e.offsetY / (cachedH || 1)) * 2 - 1
        eng.character.setLookTarget(nx, ny)
      }

      const onPointerLeave = () => {
        eng.character.setLookTarget(0, 0)
      }

      eng.canvas.addEventListener('pointermove', onPointerMove)
      eng.canvas.addEventListener('pointerleave', onPointerLeave)

      // 程序化视线目标：设置即看向、清除即回中（指针下一次移动会重新接管）
      const unsubGaze = $gazeTarget.listen(gaze => {
        if (gaze) {
          eng.character.setLookTarget(gaze.nx, gaze.ny)
        } else {
          eng.character.setLookTarget(0, 0)
        }
      })

      // 渲染功率档位 —— 启动时立即解析一次（在首个模型稳定前锁定 active），
      // 之后随信号变化更新。
      const unsubPower = subscribePowerProfile(profile => eng.setPowerProfile(profile))

      // 拖拽速度监听器：拖动窗口时让 3D 物理跟随倾斜 / 摆动
      const unsubDragVelocity = $dragVelocity.listen(vel => {
        eng.character.setDragVelocity(vel.vx, vel.vy)
      })

      // 移动肢体动画（DESIGN §3.3）：2D 由 2D 路径 locomotion 覆盖层驱动，3D 侧在
      // walk/walk_fast 移动时播放 walk clip（fly 无专属 clip，维持状态 clip）。
      const unsubLocomotion = $spatialLocomotion.listen(loco => {
        eng.character.playLocomotion(loco === 'walk' || loco === 'walk_fast' ? 'walk' : null)
      })

      // 3D 模式下像素级精确的鼠标穿透细化（静态图片用自己的命中图 —— 见 sprite-stage）。
      const detachSilhouette = attachSilhouetteHitProbe(eng)

      eng.start()

      detachWiring = () => {
        unsubState()
        unsubEmotion()
        unsubGenerated()
        detachLipSync()
        unsubPower()
        unsubDragVelocity()
        unsubLocomotion()
        unsubGaze()
        detachSilhouette()
        window.removeEventListener('resize', onResize)
        ro.disconnect()
        eng.canvas.removeEventListener('pointermove', onPointerMove)
        eng.canvas.removeEventListener('pointerleave', onPointerLeave)
      }

      return created
    })

    engineReadyRef.current = ready

    void ready.catch(err => {
      // 整条降级链都失败了 —— 完全拿不到 GPU 上下文。置为失败态交给 root
      // 渲染级联（2D 就绪则改走 mesh2d，见 DESIGN §1.2 级联）；这里置为
      // settled，调度器可以歇下来。
      if (!cancelled) {
        log.error('companion-3d', 'engine init failed:', err)
        $glbLoadFailed.set(true)
        $modelLoadSettled.set(true)
      }
    })

    return () => {
      cancelled = true
      detachWiring?.()
      engine?.dispose()
    }
  }, [])

  // 在模型 asset URL 变化时加载（或重新加载）GLB。等待引擎启动完成 —— 引擎为 null 时提前 return，
  // 否则会永久静默跳过首个模型。
  useEffect(() => {
    let cancelled = false
    const url = modelInfo.asset_url

    // 通过 IPC 拉取带签名的字节 —— 主进程重定 host，因此不触发 CORS 预检。
    // 当带 content_hash 时复用磁盘缓存并支持 Range 续传。
    // 拉取失败时返回 null，让 CharacterController 回退到程序化模型。
    void (async () => {
      const engine = await engineReadyRef.current

      if (!engine || cancelled) {
        return
      }

      let bytes: ArrayBuffer | null = null

      if (url) {
        try {
          // 先走 OPFS 缓存；失败则走主进程 IPC，成功后回填缓存。
          // 首次加载仍要走一次 IPC 往返；之后相同 contentHash 的加载瞬时完成。
          bytes = await fetchGlbWithCache(url, modelInfo.content_hash || undefined)

          if (cancelled) {
            return
          }
        } catch (err) {
          if (cancelled) {
            return
          }

          log.warn('companion-3d', 'GLB fetch failed:', err)
        }
      }

      let identity: string | null = modelInfo.content_hash || null

      if (!identity && url) {
        try {
          identity = new URL(url, 'http://127.0.0.1').pathname
        } catch {
          identity = url
        }
      }

      if (url && !bytes && identity && loadedIdentityRef.current === identity) {
        log.warn('companion-3d', 'GLB fetch failed; preserving currently rendered character model')

        return
      }

      try {
        const info = await engine.loadCharacter(
          bytes,
          modelInfo.rig_type || 'biped',
          modelInfo.content_hash || undefined
        )

        if (cancelled) {
          return
        }

        if (!info.procedural) {
          loadedIdentityRef.current = identity
        }

        // 发布引擎是否回退到了程序化的"蛋" —— 静态模式等的不是 model.ready，而是这个，
        // 因此只有 GLB 真正解析完才会切到 3D（不会出现蛋形态闪现）。
        $glbLoadFailed.set(info.procedural)
        $modelLoadSettled.set(true)

        // 用刚加载的角色立刻预热实时轮廓命中图，并顺手发布可见内容包围盒。
        void engine.silhouetteHitmap().then(map => {
          if (map) {
            $spriteContentRect.set(alphaMapContentRect(map))
            probeInteractiveRegions()
          }
        })
      } catch (err) {
        if (cancelled) {
          return
        }

        log.error('companion-3d', 'loadCharacter failed:', err)
        $glbLoadFailed.set(true)
        $modelLoadSettled.set(true)

        return
      }

      if (cancelled) {
        return
      }
    })()

    return () => {
      cancelled = true
      $spriteContentRect.set(null)
    }
  }, [modelInfo.asset_url, modelInfo.content_hash, modelInfo.id, modelInfo.rig_type])

  const genState = useStore($modelGenState)
  const genProgress = useStore($modelGenProgress)
  const genError = useStore($modelGenError)
  const retryable = useStore($modelRetryable)
  const retryModelId = useStore($modelRetryModelId)

  // 失败面板承载重试按钮 —— 没有注册区域时，点击会从透明精灵窗口直接穿透到桌面
  // （interactive-regions.ts 负责捕获开关）。面板未挂载时矩形为 null，
  // 因此交互区域仅在失败状态下注册。生成态面板为纯信息展示（pointer-events: none），不注册捕获区域以保证桌面点击穿透。
  const failedPanelRef = useRef<HTMLDivElement | null>(null)
  useInteractiveRegion('model-gen-failed', failedPanelRef)

  return (
    <div
      className="companion-3d-wrapper"
      ref={containerRef}
      style={{ position: 'relative', width: '100%', height: '100%' }}
    >
      {genState === 'generating' && (
        <GenStatusFrame>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              padding: '8px 16px',
              borderRadius: '16px',
              background: 'rgba(0, 0, 0, 0.7)',
              backdropFilter: 'blur(8px)',
              border: '1px solid rgba(255, 255, 255, 0.15)',
              boxShadow: '0 4px 20px rgba(0, 0, 0, 0.4)',
              maxWidth: '85%'
            }}
          >
            <div
              style={{
                fontSize: '0.7rem',
                color: 'rgba(255, 255, 255, 0.95)',
                marginBottom: '0.4rem',
                fontWeight: 500
              }}
            >
              ✨ 正在为你塑造形象…
            </div>
            <div
              style={{
                width: '100px',
                height: '3px',
                background: 'rgba(255, 255, 255, 0.2)',
                borderRadius: '2px',
                overflow: 'hidden'
              }}
            >
              <div
                style={{
                  width: `${genProgress?.progress ?? 0}%`,
                  height: '100%',
                  background: 'rgba(255, 255, 255, 0.9)',
                  borderRadius: '2px',
                  transition: 'width 0.5s ease'
                }}
              />
            </div>
            {genProgress?.stage && (
              <div style={{ fontSize: '0.6rem', color: 'rgba(255, 255, 255, 0.65)', marginTop: '0.3rem' }}>
                {stageLabel(genProgress.stage)}
              </div>
            )}
          </div>
        </GenStatusFrame>
      )}
      {genState === 'failed' && (
        <GenStatusFrame>
          <div
            ref={failedPanelRef}
            style={{
              padding: '6px 14px',
              borderRadius: '14px',
              background: 'rgba(0, 0, 0, 0.7)',
              backdropFilter: 'blur(8px)',
              border: '1px solid rgba(255, 100, 100, 0.25)',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.3)',
              maxWidth: '85%'
            }}
          >
            <div style={{ fontSize: '0.65rem', color: 'rgba(255, 180, 180, 0.95)', textAlign: 'center' }}>
              {genError ?? '3D 模型生成失败'}
            </div>
            {retryable && (
              <button
                onClick={() => retryModelId !== null && void retryModelDownload(retryModelId)}
                style={{
                  marginTop: '0.45rem',
                  pointerEvents: 'auto',
                  padding: '4px 14px',
                  fontSize: '0.65rem',
                  color: 'rgba(255, 255, 255, 0.95)',
                  background: 'rgba(90, 140, 255, 0.35)',
                  border: '1px solid rgba(140, 180, 255, 0.5)',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  width: '100%'
                }}
                type="button"
              >
                重试下载
              </button>
            )}
          </div>
        </GenStatusFrame>
      )}
    </div>
  )
}

const STAGE_LABELS: Record<string, string> = {
  uploading: '上传种子图…',
  generating: '生成 3D 几何…',
  rigging: '绑骨中…',
  animate_binding: '绑动画中…',
  validating: '校验模型…',
  downloading: '下载模型…',
  done: '完成！'
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}

function GenStatusFrame({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 20,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        pointerEvents: 'none',
        padding: '1rem'
      }}
    >
      {children}
    </div>
  )
}
