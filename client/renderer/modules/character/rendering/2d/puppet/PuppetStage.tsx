/** PuppetStage — puppet 的生产挂载与驱动层（Phase 6）。
 *
 * 数据源 $puppetInfo（kind=psd manifest 分流）。装配后五路驱动，全部走 PuppetRuntime
 * 的 target/auto 注入面（mesh2d 驱动层同构契约）：
 * - hitmap：当前帧部件网格精确命中 → $mesh2dHitmap —— SpriteStage 的 tap/hover/手势
 *   管线与 interaction.ts 区域语义原样复用（区域=最上层命中部件的映射）；
 * - 视线：窗口 pointermove → setGaze（$gazeTarget 显式目标优先，周期重注入续 TTL）；
 * - 说话：TTS 振幅接管 mouthOpen，静默后交还合成说话；
 * - 情绪：$spriteEmotion → 眉/嘴型/眼参数映射（mesh2d 无面部通道，puppet 独有）；
 * - 动作/交互：$spriteAction 通用语义子集 → 定时包络；hover 发区 → hairImpulse。
 * 装配失败调 setPuppetError 熄灭 $puppetReady，root 渲染级联落 3D / 蛋兜底。
 */

import { useStore } from '@nanostores/react'
import { clamp } from '@runtime'
import { useCallback, useEffect, useRef } from 'react'

import {
  $contextMenuOpen,
  $dragVelocity,
  $edgeDockSide,
  $gazeTarget,
  $isEdgeDocked,
  $spatialLocomotion,
  $spatialPos,
  $spriteAction,
  $spriteActionQueue,
  $spriteContentRect,
  $spriteEmotion,
  type Locomotion,
  probeInteractiveRegions
} from '@/modules/character'
import { registerAmplitudeSink } from '@/modules/speech'
import { log } from '@/shared/lib/log'

import { setMesh2DHitmap } from '../mesh2d/mesh2d-store'
import { fetchPsdWithCache } from '../mesh2d/psd-opfs-cache'

import { ACTION_IMPULSE_DEFAULTS, ACTION_SMOOTHED_DEFAULTS, type ActionEnvelope, ACTIONS } from './actions'
import { DragDriver } from './drag'
import { EdgePoseCanvas, type EdgePoseCanvasHandle } from './EdgePoseCanvas'
import { GaitDriver } from './gait'
import type { PuppetRuntime } from './puppet-runtime'
import { $puppetInfo, setPuppetError } from './puppet-store'
import type { Rig } from './puppet-types'
import { PuppetCanvas, type PuppetCanvasHandle } from './PuppetCanvas'

const REDUCED_MOTION_QUERY =
  typeof window !== 'undefined' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null

// hitmap：当前帧部件网格精确命中 → mesh2d 交互区域
const PART_REGION: Record<string, string> = {
  face: 'face',
  eyewhite: 'face',
  irides: 'face',
  eyelash: 'face',
  eye_close: 'face',
  eyebrow: 'face',
  nose: 'face',
  mouth_open: 'face',
  mouth_close: 'face',
  facedetail: 'face',
  ears: 'head',
  earwear: 'head',
  headwear: 'head',
  'front hair': 'front_hair',
  'back hair': 'back_hair',
  neck: 'body',
  body: 'body',
  skin: 'body',
  torso: 'body',
  arm: 'body',
  arms: 'body',
  leg: 'body',
  legs: 'body',
  topwear: 'body',
  handwear: 'body',
  footwear: 'body',
  bottomwear: 'skirt',
  legwear: 'skirt'
}

// 情绪 → 面部参数（只写 target 通道；眨眼自动化用 min 合成不会顶掉 squint）
type FaceParams = Partial<
  Pick<
    PuppetRuntime['target'],
    | 'mouthForm'
    | 'mouthOpen'
    | 'brow'
    | 'browAngSym'
    | 'eyeCY'
    | 'eyeScaleL'
    | 'eyeScaleR'
    | 'irisScale'
    | 'eyeOpenL'
    | 'eyeOpenR'
  >
>

const EMOTION_DEFAULTS: Required<FaceParams> = {
  mouthForm: 0,
  mouthOpen: 0,
  brow: 0,
  browAngSym: 0,
  eyeCY: 0,
  eyeScaleL: 1,
  eyeScaleR: 1,
  irisScale: 1,
  eyeOpenL: 1,
  eyeOpenR: 1
}

// 情绪词表与 Backend BUILTIN_EMOTIONS（services/companion/emotions.py）对齐 + 少量别名
const EMOTION_PARAMS: Record<string, FaceParams> = {
  happy: { mouthForm: 0.8, brow: 0.25, eyeCY: 0.12 },
  joy: { mouthForm: 0.8, brow: 0.25, eyeCY: 0.12 },
  cheerful: { mouthForm: 0.7, brow: 0.2 },
  sad: { mouthForm: -0.6, brow: -0.35, browAngSym: 0.28 },
  surprised: { eyeScaleL: 1.12, eyeScaleR: 1.12, irisScale: 1.1, brow: 0.5, mouthOpen: 0.22 },
  excited: { mouthForm: 0.9, brow: 0.35, eyeCY: 0.15, eyeScaleL: 1.06, eyeScaleR: 1.06 },
  confused: { browAngSym: 0.35, eyeCY: 0.08, mouthForm: -0.2 },
  concerned: { brow: -0.25, browAngSym: 0.3, mouthForm: -0.35 },
  shy: { eyeOpenL: 0.72, eyeOpenR: 0.72, mouthForm: 0.35, eyeCY: 0.1 },
  proud: { browAngSym: -0.25, eyeCY: 0.06, mouthForm: 0.2 },
  grateful: { mouthForm: 0.6, brow: 0.2, eyeOpenL: 0.85, eyeOpenR: 0.85 },
  playful: { mouthForm: 0.7, browAngSym: -0.3, eyeCY: 0.1 },
  bored: { eyeOpenL: 0.55, eyeOpenR: 0.55, brow: -0.15, mouthForm: -0.15 },
  lonely: { mouthForm: -0.4, brow: -0.2, eyeCY: -0.08 },
  sleepy: { eyeOpenL: 0.45, eyeOpenR: 0.45, brow: -0.1, mouthOpen: 0.14 },
  curious: { brow: 0.3, irisScale: 1.05, browAngSym: 0.15 },
  embarrassed: { eyeOpenL: 0.7, eyeOpenR: 0.7, browAngSym: 0.4, mouthForm: 0.15 },
  apologetic: { brow: -0.3, browAngSym: 0.3, mouthForm: -0.3, eyeOpenL: 0.85, eyeOpenR: 0.85 },
  pout: { mouthForm: -0.5, browAngSym: 0.45, brow: -0.2, mouthOpen: 0.12 },
  angry: { brow: -0.6, browAngSym: 0.5, mouthForm: -0.3 },
  smug: { browAngSym: -0.35, eyeOpenL: 0.8, eyeOpenR: 0.8, mouthForm: 0.4 },
  scared: { eyeScaleL: 1.15, eyeScaleR: 1.15, irisScale: 1.08, brow: 0.4, mouthOpen: 0.18 },
  relieved: { eyeOpenL: 0.75, eyeOpenR: 0.75, brow: 0.15, mouthForm: 0.3 },
  neutral: {}
}

function applyEmotion(rt: PuppetRuntime, emotion: string | null): void {
  const params = emotion ? (EMOTION_PARAMS[emotion] ?? null) : EMOTION_PARAMS['neutral']!

  if (!params) {
    return // 未知情绪键不动面部（与 mesh2d 忽略未注册 action 同一策略）
  }

  for (const key of Object.keys(EMOTION_DEFAULTS) as (keyof typeof EMOTION_DEFAULTS)[]) {
    rt.target[key] = (params[key] ?? EMOTION_DEFAULTS[key]) as never
  }
}

// 可见内容包围盒上报：rig 层矩形并集（rig 坐标）→ 归一化舞台盒。canvas 经
// max-w/h-full 在舞台盒内 contain-fit 居中，rig 坐标按同一几何映射。
function publishPuppetContentRect(rig: Rig, container: HTMLElement | null): void {
  const boxW = container?.clientWidth || 0
  const boxH = container?.clientHeight || 0

  if (boxW <= 0 || boxH <= 0 || rig.layers.length === 0) {
    return
  }

  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity

  for (const L of rig.layers) {
    x0 = Math.min(x0, L.x)
    y0 = Math.min(y0, L.y)
    x1 = Math.max(x1, L.x + L.w)
    y1 = Math.max(y1, L.y + L.h)
  }

  const fit = boxH / rig.canvas.h
  const offX = (boxW - rig.canvas.w * fit) / 2
  const offY = 0

  $spriteContentRect.set({
    left: (offX + x0 * fit) / boxW,
    top: (offY + y0 * fit) / boxH,
    right: (offX + x1 * fit) / boxW,
    bottom: (offY + y1 * fit) / boxH
  })
}

export function PuppetStage(): React.JSX.Element {
  const handleRef = useRef<PuppetCanvasHandle>(null)
  const edgePoseRef = useRef<EdgePoseCanvasHandle>(null)
  const showingPoseRef = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)
  // 命中换算需要画布的 contain-fit 矩形；挂载期一次性接线（稳定引用，避免触发运行时重建）
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  const onCanvas = useCallback((canvas: HTMLCanvasElement): void => {
    canvasRef.current = canvas
  }, [])

  // 驱动层共享的运行态（rAF 循环每帧读取）
  const hitmapRef = useRef<((nx: number, ny: number) => { region: string } | null) | null>(null)
  const ampRef = useRef(0)
  const lastTalkAtRef = useRef(0)
  const wasTalkingRef = useRef(false)
  const gazeTargetRef = useRef<{ nx: number; ny: number } | null>(null)
  const lastGazeInjectRef = useRef(0)
  const lastHairImpulseAtRef = useRef(0)
  const actionRef = useRef<{ env: ActionEnvelope; t0: number } | null>(null)
  const dragDriverRef = useRef(new DragDriver())
  const dragVelocityAtRef = useRef(0)
  const gaitDriverRef = useRef<GaitDriver>(new GaitDriver())
  const spatialLocomotionRef = useRef<Locomotion>($spatialLocomotion.get())
  const isEdgeDockedRef = useRef<boolean>($isEdgeDocked.get())
  const edgeDockSideRef = useRef<'none' | 'left' | 'right'>($edgeDockSide.get())
  const spatialPosRef = useRef<{ x: number; y: number }>($spatialPos.get())
  const lastPosRef = useRef<{ x: number; y: number }>($spatialPos.get())
  const lastTickTimeRef = useRef<number>(performance.now())

  const emotionEyeCYRef = useRef<number>(EMOTION_PARAMS[$spriteEmotion.get() ?? 'neutral']?.eyeCY ?? 0)

  const puppet = useStore($puppetInfo)

  // PSD 装配 + hitmap 上线
  useEffect(() => {
    if (!puppet.psdUrl) {
      return
    }

    const abortController = new AbortController()

    void (async () => {
      try {
        const buffer = await fetchPsdWithCache(puppet.psdUrl!, puppet.contentHash, abortController.signal)

        if (abortController.signal.aborted) {
          return
        }

        const rig = await handleRef.current?.loadPsd(buffer)

        if (!rig || abortController.signal.aborted) {
          return
        }

        // 命中 = 当前帧可见像素：舞台归一化坐标先经 contain-fit 画布矩形换算成 rig
        // 画布像素，再由 runtime 自顶向下逐层网格点测（层矩形 bbox 会把部件四周的
        // 透明留白也算命中——透明窗口下即"看不见也能点"，见 companion README §7）。
        const hit = (nx: number, ny: number): { region: string } | null => {
          const rt = handleRef.current?.runtime
          const canvas = canvasRef.current
          const box = containerRef.current

          if (!rt || !canvas || !box) {
            return null
          }

          const br = box.getBoundingClientRect()

          if (showingPoseRef.current) {
            return edgePoseRef.current?.hit(br.left + nx * br.width, br.top + ny * br.height) ?? null
          }

          const cr = canvas.getBoundingClientRect()

          if (cr.width <= 0 || cr.height <= 0) {
            return null
          }

          const cx = (nx * br.width + br.left - cr.left) / cr.width
          const cy = (ny * br.height + br.top - cr.top) / cr.height

          if (cx < 0 || cx > 1 || cy < 0 || cy > 1) {
            return null
          }

          const bn = rt.hitPart(cx * canvas.width, cy * canvas.height)

          return bn ? { region: PART_REGION[bn] ?? 'body' } : null
        }

        hitmapRef.current = hit
        setMesh2DHitmap({ hit })
        publishPuppetContentRect(rig, containerRef.current)
        probeInteractiveRegions()

        const rt = handleRef.current?.runtime

        if (rt) {
          if (REDUCED_MOTION_QUERY?.matches === true) {
            rt.auto.idle = false
            rt.auto.rand = false
          }

          log.info('puppet-stage', `psd rigged: ${rig.layers.length} parts, tier=${rt.rigTier()}`)
        }
      } catch (err) {
        if (abortController.signal.aborted) {
          return
        }

        log.warn('puppet-stage', 'psd load failed; cascade to 3D / egg', err)
        setPuppetError(err instanceof Error ? err.message : String(err))
      }
    })()

    return () => {
      abortController.abort()
      hitmapRef.current = null
      setMesh2DHitmap(null)
      $spriteContentRect.set(null)
      probeInteractiveRegions()
    }
  }, [puppet.psdUrl, puppet.contentHash])

  useEffect(() => {
    let raf = 0

    const rt = (): PuppetRuntime | null => handleRef.current?.runtime ?? null

    const onMove = (e: PointerEvent): void => {
      const r = rt()

      if (!r) {
        return
      }

      if ($contextMenuOpen.get()) {
        r.setGaze(null)

        return
      }

      const rect = containerRef.current?.getBoundingClientRect()

      if (!rect) {
        return
      }

      const lx = (e.clientX - rect.left) / rect.width
      const ly = (e.clientY - rect.top) / rect.height
      const nx = clamp(lx * 2 - 1, -1, 1)
      // 纵向参考面部高度（画布上 35%），避免视线永远俯视
      const ny = clamp((ly - 0.35) * 2, -1, 1)
      r.setGaze(nx * 0.9, ny)

      // hover 发区 → 发束链冲量（200ms 节流）
      const hit = hitmapRef.current?.(lx, ly)
      const region = hit?.region

      if (
        (region === 'front_hair' || region === 'back_hair') &&
        performance.now() - lastHairImpulseAtRef.current > 200
      ) {
        lastHairImpulseAtRef.current = performance.now()
        r.hairImpulse((region === 'front_hair' ? 1.6 : 1.1) * (lx < 0.5 ? 1 : -1))
      }
    }

    const onGrab = (e: PointerEvent): void => {
      if (e.button !== 0) {
        return
      }

      const canvas = canvasRef.current
      const rect = canvas?.getBoundingClientRect()

      if (canvas && rect && rect.width > 0 && rect.height > 0) {
        rt()?.setGrabPoint(
          ((e.clientX - rect.left) / rect.width) * canvas.width,
          ((e.clientY - rect.top) / rect.height) * canvas.height
        )
      }
    }

    const onLeave = (): void => {
      rt()?.setGaze(null)
    }

    window.addEventListener('pointerdown', onGrab, true)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerleave', onLeave)

    const stopAmp = registerAmplitudeSink(amp => {
      ampRef.current = amp
    })

    const unsubEmotion = $spriteEmotion.listen(emotion => {
      const r = rt()

      if (r) {
        applyEmotion(r, emotion)
        emotionEyeCYRef.current = EMOTION_PARAMS[emotion ?? 'neutral']?.eyeCY ?? 0
      }
    })

    const unsubAction = $spriteAction.listen(action => {
      const r = rt()

      if (!r) {
        return
      }

      const env = action ? ACTIONS[action] : undefined

      if (env) {
        env.onStart?.(r)
        actionRef.current = { env, t0: performance.now() }
      }
    })

    const unsubGazeTarget = $gazeTarget.listen(target => {
      gazeTargetRef.current = target

      if (target) {
        rt()?.setGaze(target.nx, target.ny)
      }
    })

    const unsubDragVelocity = $dragVelocity.listen(() => {
      dragVelocityAtRef.current = performance.now()
    })

    const unsubLocomotion = $spatialLocomotion.listen(loco => {
      spatialLocomotionRef.current = loco
    })

    const unsubDocked = $isEdgeDocked.listen(docked => {
      isEdgeDockedRef.current = docked
    })

    const unsubDockSide = $edgeDockSide.listen(side => {
      edgeDockSideRef.current = side
    })

    const unsubPos = $spatialPos.listen(pos => {
      spatialPosRef.current = pos
    })

    const tick = (now: number): void => {
      const r = rt()

      if (r) {
        const dt = Math.min(0.05, (now - lastTickTimeRef.current) / 1000)
        lastTickTimeRef.current = now

        const curPos = spatialPosRef.current
        let dx = curPos.x - lastPosRef.current.x
        let dy = curPos.y - lastPosRef.current.y

        if (Math.hypot(dx, dy) > 100) {
          dx = 0
          dy = 0
        }

        lastPosRef.current = curPos

        // 1. 步态驱动与趴姿计算
        const gaitOut = gaitDriverRef.current.update(
          dt,
          dx,
          dy,
          spatialLocomotionRef.current,
          isEdgeDockedRef.current,
          edgeDockSideRef.current,
          r.rigTier()
        )

        // 基线和步态先写入；接触约束在动作包络之后裁决。
        const w = gaitOut.clingWeight
        const gA = gaitOut.gaitEnvelope
        const gOff = gaitOut.gaitOffsets

        // 步态 overlay（加法）；步态不写 angleY（俯仰通道），仅趴姿经 lerp 注入
        let body = gOff.body
        let angleZ = gOff.angleZ
        let angleY = 0
        let angleX = gOff.angleX
        let fhAmp = 2 + gOff.fhAmp
        let physAmp = 2 + gOff.physAmp

        // 趴姿 overlay（lerp 权重混合，盖过残余步态）
        if (gaitOut.clingPose && w > 0) {
          const cp = gaitOut.clingPose
          body = body * (1 - w) + cp.body * w
          angleY = angleY * (1 - w) + cp.angleY * w
          angleZ = angleZ * (1 - w) + cp.angleZ * w
          angleX = angleX * (1 - w) + cp.angleX * w
          fhAmp += cp.fhAmp * w
          physAmp += cp.physAmp * w

          const baseEyeCY = emotionEyeCYRef.current
          r.target.eyeCY = baseEyeCY * (1 - w) + (baseEyeCY + cp.eyeCY) * w
        } else {
          r.target.eyeCY = emotionEyeCYRef.current
        }

        r.target.body = body
        r.target.angleZ = angleZ
        r.target.angleY = angleY
        r.target.angleX = angleX
        r.target.fhAmp = fhAmp
        r.target.physAmp = physAmp

        r.target.footIKL = gaitOut.footIK.l
        r.target.footIKR = gaitOut.footIK.r
        r.target.armSwingL = gaitOut.armSwing.l
        r.target.armSwingR = gaitOut.armSwing.r
        r.target.handIKL = null
        r.target.handIKR = null

        // 3. auto.idle 待机呼吸幅度衰减（走路 0.7，趴姿 0.4）
        r.idleScale = gaitOut.idleScale

        // 4. TTS 振幅接管嘴型；静默 600ms 后交还合成说话
        if (ampRef.current > 0.04) {
          lastTalkAtRef.current = now

          if (!wasTalkingRef.current) {
            wasTalkingRef.current = true
            r.auto.talk = false
          }

          r.target.mouthOpen = Math.min(1, 0.18 + ampRef.current * 0.9)
        } else if (wasTalkingRef.current && now - lastTalkAtRef.current > 600) {
          wasTalkingRef.current = false
          r.auto.talk = true
          r.target.mouthOpen = 0
        }

        // 5. 显式视线目标周期重注入（setGaze 3s TTL；ritual walk 途中持续锁定）
        const gt = gazeTargetRef.current

        if (gt && now - lastGazeInjectRef.current > 800) {
          lastGazeInjectRef.current = now
          r.setGaze(gt.nx, gt.ny)
        }

        // 6. 动作包络推进；结束帧把触及通道写回默认值并续播队列（mesh2d driver 同构）
        const a = actionRef.current

        if (a) {
          const k = (now - a.t0) / a.env.durMs

          if (k >= 1) {
            a.env.apply(1, r)

            // 冲量式通道：动作结束立即归零，下一帧平滑器接管
            for (const [key, v] of Object.entries(ACTION_IMPULSE_DEFAULTS) as [
              keyof PuppetRuntime['target'],
              unknown
            ][]) {
              r.target[key] = v as never
            }

            // 平滑式 IK 通道：只在步态/贴边都已经基本衰减时才写回默认值。
            // 否则贴边手、单脚离地的 swing 会被一帧拉回 bind pose。
            if (gA < 0.05 && w < 0.05) {
              for (const [key, v] of Object.entries(ACTION_SMOOTHED_DEFAULTS) as [
                keyof PuppetRuntime['target'],
                unknown
              ][]) {
                r.target[key] = v as never
              }
            }

            actionRef.current = null

            const queue = $spriteActionQueue.get()

            if (queue.length > 0) {
              const [next, ...rest] = queue
              $spriteActionQueue.set(rest)

              const env = next ? ACTIONS[next] : undefined

              if (env) {
                env.onStart?.(r)
                actionRef.current = { env, t0: now }
              }
            }
          } else {
            a.env.apply(k, r)
          }
        }

        const dragging = spatialLocomotionRef.current === 'drag'
        const velocity = $dragVelocity.get()
        const fresh = Math.exp(-Math.max(0, now - dragVelocityAtRef.current - 40) / 80)
        const drag = dragDriverRef.current
        drag.update(dt, dragging, velocity.vx * fresh, velocity.vy * fresh)
        const motionScale = REDUCED_MOTION_QUERY?.matches ? 0.2 : 1
        const dw = drag.weight * motionScale
        r.target.suspendAngle = drag.angle * motionScale
        r.target.suspendStretch = 1 + drag.lift * 0.018 * motionScale
        r.target.suspendWeight = drag.weight

        const poseSide =
          isEdgeDockedRef.current && spatialLocomotionRef.current === 'still' ? edgeDockSideRef.current : 'none'

        const wasShowing = showingPoseRef.current
        showingPoseRef.current =
          edgePoseRef.current?.update(poseSide, dt, now, angleY, REDUCED_MOTION_QUERY?.matches === true) ?? false

        if (canvasRef.current) {
          canvasRef.current.style.visibility = showingPoseRef.current ? 'hidden' : 'visible'
        }

        if (wasShowing !== showingPoseRef.current) {
          probeInteractiveRegions()
        }

        if (dragging || drag.weight > 0.001 || isEdgeDockedRef.current || w > 0.001) {
          // 接触和悬挂姿态独占四肢；正面抬脚 IK 会把悬空双腿掰成外八。
          r.target.handIKL = null
          r.target.handIKR = null
          r.target.footIKL = null
          r.target.footIKR = null
          r.target.armSwingL = -0.1 * dw + drag.angle * 0.2 * motionScale
          r.target.armSwingR = 0.1 * dw + drag.angle * 0.2 * motionScale
          r.target.body = body * (1 - drag.weight)
          r.target.angleZ = angleZ * (1 - drag.weight)
          r.target.angleY = angleY - dw * 0.08
          r.target.angleX = angleX
          r.idleScale *= 1 - Math.max(drag.weight, w) * 0.8
        }
      }

      raf = requestAnimationFrame(tick)
    }

    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('pointerdown', onGrab, true)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerleave', onLeave)
      stopAmp()
      unsubEmotion()
      unsubAction()
      unsubGazeTarget()
      unsubDragVelocity()
      unsubLocomotion()
      unsubDocked()
      unsubDockSide()
      unsubPos()
    }
  }, [])

  return (
    <div
      className="flex h-full w-full items-center justify-center overflow-visible"
      ref={containerRef}
      style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
    >
      <PuppetCanvas onCanvas={onCanvas} ref={handleRef} />
      {puppet.poses && <EdgePoseCanvas key={puppet.contentHash} pack={puppet.poses} ref={edgePoseRef} />}
    </div>
  )
}
