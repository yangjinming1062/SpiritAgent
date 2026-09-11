import { clamp } from '@runtime'
import * as THREE from 'three'
import { WebGPUBackend, WebGPURenderer } from 'three/webgpu'

import { getBaseSpriteHeight, getBaseSpriteWidth } from '@/companion'
import { log } from '@/shared/lib/log'

import { CharacterController } from './CharacterController'
import { reportBackend, reportEngineError, reportFrameStats } from './engine-diagnostics'
import { LightingRig } from './LightingRig'
import type { PowerProfile } from './PowerProfile'
import { PROFILE_FPS } from './PowerProfile'
import type { EngineBackendKind, EngineOptions, LoadedModelInfo } from './types'

type AnyRenderer = THREE.WebGLRenderer | WebGPURenderer

// dormant 档位由定时器以 4fps 驱动，而非 rAF：
// 锁屏与被遮挡窗口会完全停止 rAF，而本进程关闭了 Chromium 定时器限流，
// 因此 setTimeout 仍能保持稳定节奏。
const DORMANT_TICK_MS = 250
// 即使加了反限流开关，隐藏窗口仍会硬性停止 rAF。
// 处于活跃档位且被遮挡的伙伴（例如正在说话）会改用定时器继续推进动画，
// 直到窗口恢复可见。
const HIDDEN_ACTIVE_MS = 16
const HIDDEN_IDLE_MS = 37
// 唤醒钳位——dormant→active 切换时不能把多秒级的 delta 喂给 mixer。
const MAX_FRAME_DELTA = 0.05

// DPR 上限。1.5 对 300×360 的桌面伙伴窗口已经足够——
// 再高（如 2.0）会把 shader 工作量翻倍，
// 但在精灵原生显示尺寸下肉眼无差别。iGPU + alpha:true 受 fillrate 限制。
const MAX_DPR = 1.5

// 剪影命中图：1/4 canvas 分辨率上限限定额外渲染与回读开销；
// TTL 在光标扫过精灵矩形时将刷新频率限制为 4 Hz。
const HITMAP_SCALE = 4
const HITMAP_TTL_MS = 250

// 允许连续瞬态渲染错误的上限，超过后才彻底停转 ticker，避免偶发 WebGL 异常永久冻屏。
const MAX_CONSECUTIVE_TICK_ERRORS = 5

export interface SilhouetteHitmap {
  alpha: Uint8Array
  width: number
  height: number
}

function makeCanvas(container: HTMLElement): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.className = 'companion-3d-canvas'
  canvas.style.width = '100%'
  canvas.style.height = '100%'
  canvas.style.display = 'block'
  container.appendChild(canvas)

  return canvas
}

function readCanvasSize(canvas: HTMLCanvasElement): { width: number; height: number } {
  const parent = canvas.parentElement
  const width = parent?.clientWidth || canvas.clientWidth || getBaseSpriteWidth()
  const height = parent?.clientHeight || canvas.clientHeight || getBaseSpriteHeight()

  return { width, height }
}

export class Engine {
  private readonly renderer: AnyRenderer
  private readonly backendKind: EngineBackendKind
  readonly canvas: HTMLCanvasElement
  private readonly scene: THREE.Scene
  private readonly camera: THREE.PerspectiveCamera
  private readonly clock = new THREE.Clock()
  readonly character: CharacterController
  private readonly lighting: LightingRig
  /** Measured render rate, refreshed once per second (diagnostics only). */
  private readonly stats = { fps: 0 }

  private rafId: number | null = null
  private timerId: ReturnType<typeof setTimeout> | null = null
  private disposed = false
  private running = false
  private consecutiveErrors = 0
  private isTicking = false
  private profile: PowerProfile = 'active'
  private lastFrameAt = 0
  private statsFrames = 0
  private statsWindowStart = 0
  private hitRT: THREE.RenderTarget | null = null
  private hitMap: SilhouetteHitmap | null = null
  private hitMapAt = 0
  private hitRefresh: Promise<SilhouetteHitmap | null> | null = null

  // 异步工厂：WebGPURenderer.init() 负责回退链的前两档
  // （WebGPU 后端 → 自带的 WebGL2 重试）。只有 init 完全失败
  // 才会降级到经典 WebGLRenderer——前提是新 canvas，
  // 因为一旦承载过 webgpu 上下文的 canvas 不会再产出 webgl2 上下文。
  // 进一步失败则向上抛给调用方，由 root 渲染级联兜底（DESIGN §1.2）。
  static async create(container: HTMLElement, opts: EngineOptions = {}): Promise<Engine> {
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR)
    // 默认走 iGPU：精灵窗负载远低于 iGPU 满载门槛，避免在混合显卡本上唤醒 dGPU。
    const powerPreference = opts.powerPreference ?? 'low-power'

    // ── 尝试 1：WebGPU（首选 WebGPU 后端，init 内部回退 WebGL2）──
    try {
      const canvas = makeCanvas(container)
      const { width, height } = readCanvasSize(canvas)

      const renderer = new WebGPURenderer({
        canvas,
        alpha: true,
        antialias: true,
        powerPreference
      })

      renderer.setPixelRatio(dpr)
      renderer.setSize(width, height, false)

      await renderer.init()

      const backendKind: EngineBackendKind = renderer.backend instanceof WebGPUBackend ? 'webgpu' : 'webgl2'

      log.info('3d', `Engine initialized with ${backendKind} backend (${width}x${height} @ ${dpr}x)`)

      return new Engine(renderer, backendKind, canvas, opts)
    } catch (gpuErr) {
      log.warn('3d', 'WebGPURenderer failed, falling back to classic WebGLRenderer:', gpuErr)
    }

    // ── 尝试 2：经典 WebGLRenderer（纯标准 WebGL2，兼容旧 GPU 与驱动）──
    try {
      // 必须用新 canvas：承载过 webgpu 上下文的 canvas 不会再产出 webgl2 上下文
      const canvas = makeCanvas(container)
      const { width, height } = readCanvasSize(canvas)

      const renderer = new THREE.WebGLRenderer({
        canvas,
        alpha: true,
        antialias: true,
        powerPreference,
        preserveDrawingBuffer: false
      })

      renderer.setPixelRatio(dpr)
      renderer.setSize(width, height, false)

      log.info('3d', `Engine initialized with classic-webgl backend (${width}x${height} @ ${dpr}x)`)

      return new Engine(renderer, 'classic-webgl', canvas, opts)
    } catch (glErr) {
      log.error('3d', 'All 3D renderer backends failed:', glErr)
      throw new Error(`Failed to initialize any 3D renderer: ${glErr instanceof Error ? glErr.message : String(glErr)}`)
    }
  }

  private constructor(
    renderer: AnyRenderer,
    backendKind: EngineBackendKind,
    canvas: HTMLCanvasElement,
    opts: EngineOptions = {}
  ) {
    this.renderer = renderer
    this.backendKind = backendKind
    this.canvas = canvas

    const useShadows = opts.useShadows ?? false

    if (useShadows) {
      this.renderer.shadowMap.enabled = true
      this.renderer.shadowMap.type = THREE.PCFSoftShadowMap
    }

    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.05

    this.scene = new THREE.Scene()

    const { width, height } = readCanvasSize(this.canvas)
    const aspect = width / height
    this.camera = new THREE.PerspectiveCamera(30, aspect, 0.1, 100)
    this.camera.position.set(0, 0.9, 6.0)
    this.camera.lookAt(0, 0.9, 0)

    this.lighting = new LightingRig(this.scene, this.renderer, useShadows)

    this.character = new CharacterController()
    this.character.onPoseChange = () => this.invalidateHitmap()

    reportBackend(backendKind)
  }

  /** 角色动作或姿态发生切换时立即废弃旧的命中缓存，避免 250ms 内使用走动姿态命中静止姿态。 */
  invalidateHitmap(): void {
    this.hitMap = null
    this.hitMapAt = 0
  }

  /** Current-frame silhouette alpha at ~1/4 canvas resolution. Renders the
   * scene into an offscreen target — clear alpha is 0, so only drawn pixels
   * count and the map tracks the live pose and outline hulls exactly.
   * Cached HITMAP_TTL_MS; concurrent callers share one refresh. Null only
   * when the readback itself fails. */
  async silhouetteHitmap(): Promise<SilhouetteHitmap | null> {
    if (this.disposed) {
      return null
    }

    if (this.hitMap && performance.now() - this.hitMapAt < HITMAP_TTL_MS) {
      return this.hitMap
    }

    this.hitRefresh ??= this.refreshHitmap().finally(() => {
      this.hitRefresh = null
    })

    return this.hitRefresh
  }

  private async refreshHitmap(): Promise<SilhouetteHitmap | null> {
    if (this.disposed) {
      return null
    }

    const canvasW = this.canvas.clientWidth || this.canvas.parentElement?.clientWidth || getBaseSpriteWidth()
    const canvasH = this.canvas.clientHeight || this.canvas.parentElement?.clientHeight || getBaseSpriteHeight()
    const w = Math.max(1, Math.round(canvasW / HITMAP_SCALE))
    const h = Math.max(1, Math.round(canvasH / HITMAP_SCALE))

    if (!this.hitRT || this.hitRT.width !== w || this.hitRT.height !== h) {
      this.hitRT?.dispose()
      // 经典档位的异步 read 需要 WebGLRenderTarget；节点档位接受核心 RenderTarget。
      this.hitRT =
        this.backendKind === 'classic-webgl' ? new THREE.WebGLRenderTarget(w, h) : new THREE.RenderTarget(w, h)
    }

    const rt = this.hitRT

    if (!rt) {
      return null
    }

    try {
      // 参数转换收窄渲染器联合类型——节点档位接受 RenderTarget 父类型，
      // 经典档位接受 WebGLRenderTarget，两档位在此按 kind 各自构造。
      try {
        this.renderer.setRenderTarget(rt as THREE.WebGLRenderTarget)
        this.renderer.render(this.scene, this.camera)
      } finally {
        if (!this.disposed) {
          this.renderer.setRenderTarget(null)
        }
      }

      const data =
        this.backendKind === 'classic-webgl'
          ? await this.readClassicPixels(rt as THREE.WebGLRenderTarget, w, h)
          : await (this.renderer as WebGPURenderer).readRenderTargetPixelsAsync(rt, 0, 0, w, h)

      if (this.disposed || !data) {
        return null
      }

      const alpha = new Uint8Array(w * h)
      // WebGPU 的 copyTextureToBuffer 每行按 256 字节对齐；WebGL 的 readPixels 紧凑排列。
      // 统一为自上而下的行序，与 DOM client 空间一致（y=0 在 canvas 顶部）。
      const isWebGPU = this.backendKind === 'webgpu'
      const isBottomUp = this.backendKind !== 'webgpu'
      const rowStrideBytes = isWebGPU ? Math.ceil((w * 4) / 256) * 256 : w * 4

      for (let y = 0; y < h; y++) {
        const srcY = isBottomUp ? h - 1 - y : y
        const srcRowOffset = srcY * rowStrideBytes
        const dstRowOffset = y * w

        for (let x = 0; x < w; x++) {
          alpha[dstRowOffset + x] = data[srcRowOffset + x * 4 + 3]
        }
      }

      this.hitMap = { alpha, height: h, width: w }
      this.hitMapAt = performance.now()

      return this.hitMap
    } catch (err) {
      if (!this.disposed) {
        log.warn('3d', 'silhouette hitmap readback failed:', err)
      }

      return null
    }
  }

  // 经典 WebGLRenderTarget 的异步像素回读。优先走 PBO 围栏；
  // 没有围栏扩展时回退到 readPixels 同步读取。
  private async readClassicPixels(
    rt: THREE.WebGLRenderTarget,
    width: number,
    height: number
  ): Promise<Uint8Array | null> {
    if (this.disposed) {
      return null
    }

    const glRenderer = this.renderer as THREE.WebGLRenderer
    const gl = glRenderer.getContext() as WebGL2RenderingContext

    if (!(gl instanceof WebGL2RenderingContext)) {
      const out = new Uint8Array(width * height * 4)
      glRenderer.readRenderTargetPixels(rt, 0, 0, width, height, out)

      return out
    }

    const pbo = gl.createBuffer()

    if (!pbo) {
      const out = new Uint8Array(width * height * 4)
      glRenderer.readRenderTargetPixels(rt, 0, 0, width, height, out)

      return out
    }

    const size = width * height * 4
    const glProps = glRenderer.properties.get(rt) as { __webglFramebuffer?: WebGLFramebuffer } | undefined
    const fb = glProps?.__webglFramebuffer

    if (!fb) {
      gl.deleteBuffer(pbo)

      const out = new Uint8Array(size)
      glRenderer.readRenderTargetPixels(rt, 0, 0, width, height, out)

      return out
    }

    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, fb)
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pbo)
    gl.bufferData(gl.PIXEL_PACK_BUFFER, size, gl.STREAM_READ)
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, 0)
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null)
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null)

    const sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0)

    if (!sync) {
      gl.deleteBuffer(pbo)

      const out = new Uint8Array(size)
      glRenderer.readRenderTargetPixels(rt, 0, 0, width, height, out)

      return out
    }

    await this.waitSync(gl, sync)
    gl.deleteSync(sync)

    if (this.disposed) {
      gl.deleteBuffer(pbo)

      return null
    }

    const out = new Uint8Array(size)
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, pbo)
    gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, out)
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null)
    gl.deleteBuffer(pbo)

    return out
  }

  private waitSync(gl: WebGL2RenderingContext, sync: WebGLSync): Promise<void> {
    return new Promise(resolve => {
      const check = (): void => {
        if (this.disposed) {
          resolve()

          return
        }

        const res = gl.clientWaitSync(sync, 0, 0)

        if (res === gl.ALREADY_SIGNALED || res === gl.CONDITION_SATISFIED) {
          resolve()
        } else if (res === gl.WAIT_FAILED) {
          resolve()
        } else {
          setTimeout(check, 4)
        }
      }

      check()
    })
  }

  /** 计算包围盒与相机距，对齐正对视角。 */
  frameCharacter(): void {
    const box = new THREE.Box3().setFromObject(this.character.root)

    if (box.isEmpty()) {
      return
    }

    const widthSpan = Math.max(box.max.x - box.min.x, box.max.z - box.min.z, 0.4)
    const height = Math.max(box.max.y - box.min.y, 0.6)
    const centerX = (box.min.x + box.max.x) * 0.5
    const centerY = (box.min.y + box.max.y) * 0.5

    // 将相机目标对准角色身高的 ~55% 处（胸口/锁骨中段偏上），
    // 配合下颌微收的平视视线，达成最舒适的水平对视感。
    const targetY = box.min.y + height * 0.55

    // 计算覆盖全身包围盒所需的相机距离（保留 15% 视野余量）。
    // 对于人形双足骨骼（biped），常态站姿躯干与垂臂宽度仅约为身高的 32%~35%，
    // 但静态绑定姿态（T-pose/A-pose）网格包围盒横展往往达 100% 身高以上。
    // 在窄画布/竖屏视口下若按绑定姿态 T-pose 横展计算 distW，会导致相机急剧后退、
    // 角色被严重缩放至微缩尺寸。因此对双足骨骼将有效横展上限约束在常态站姿比例，
    // 确保角色始终按全身垂直高度基准构图（稳定占画布高约 85%），尺寸恒定。
    const effectiveWidthSpan = this.character.isBipedRig ? Math.min(widthSpan, height * 0.35) : widthSpan
    const aspect = this.camera.aspect
    const halfFovRad = THREE.MathUtils.degToRad(this.camera.fov * 0.5)
    const distH = (height * 0.5) / (Math.tan(halfFovRad) * 0.85)
    const distW = (effectiveWidthSpan * 0.5) / (Math.tan(halfFovRad) * aspect * 0.85)
    const dist = Math.max(distH, distW, 0.5)

    // 水平的正面对视相机（俯仰角 0°，平视水平视线）
    this.camera.position.set(centerX, targetY, dist)
    this.camera.lookAt(centerX, targetY, 0)

    // 通过 setViewOffset 垂直偏移投影窗口，使全身从头到脚
    // 在 canvas 窗口内居中显示，而无需倾斜相机光轴。
    const canvasWidth = this.canvas.clientWidth || getBaseSpriteWidth()
    const canvasHeight = this.canvas.clientHeight || getBaseSpriteHeight()
    const visibleWorldHeight = ((height * 0.5) / 0.85) * 2
    const deltaY = targetY - centerY
    const yOffset = (deltaY / visibleWorldHeight) * canvasHeight

    if (Math.abs(yOffset) > 0.5) {
      this.camera.setViewOffset(canvasWidth, canvasHeight, 0, yOffset, canvasWidth, canvasHeight)
    } else {
      this.camera.clearViewOffset()
    }
  }

  async loadCharacter(
    bytes: ArrayBuffer | null,
    rigType: string = 'biped',
    contentHash?: string
  ): Promise<LoadedModelInfo> {
    const info = await this.character.load(bytes, this.scene, rigType, contentHash)
    this.frameCharacter()
    this.hitMap = null
    this.hitMapAt = 0

    return info
  }

  start(): void {
    if (this.running || this.disposed) {
      return
    }

    this.consecutiveErrors = 0
    this.running = true
    this.clock.getDelta()
    document.addEventListener('visibilitychange', this.onVisibilityChange)
    this.scheduleNext()
  }

  stop(): void {
    this.running = false
    this.consecutiveErrors = 0
    document.removeEventListener('visibilitychange', this.onVisibilityChange)
    this.cancelPendingLoop()
  }

  setPowerProfile(profile: PowerProfile): void {
    if (profile === this.profile) {
      return
    }

    this.profile = profile

    // 按新节奏重新调度；cancelPendingLoop 关闭旧 rAF/定时器回调仍排队时的竞态。
    if (this.running && !this.disposed) {
      this.cancelPendingLoop()
      this.scheduleNext()
    }
  }

  private onVisibilityChange = (): void => {
    // 隐藏窗口永远不会触发排队的 rAF——直接换成定时器回退，
    // 无需等待档位变更。
    if (this.running && !this.disposed) {
      this.cancelPendingLoop()
      this.scheduleNext()
    }
  }

  private tick = (): void => {
    this.rafId = null
    this.timerId = null

    if (!this.running || this.disposed || this.isTicking) {
      return
    }

    this.isTicking = true
    const now = performance.now()

    try {
      const budgetMs = 1000 / PROFILE_FPS[this.profile]
      const threshold = this.profile === 'active' ? budgetMs * 0.75 : budgetMs * 0.85

      // 帧预算允许容差（active 下 ≥12.5ms；idle 下 ≥28.3ms），
      // 让 60Hz 显示器不会因亚毫秒级抖动丢帧，
      // 让高刷显示器能精确控制在目标帧率。
      if (now - this.lastFrameAt >= threshold) {
        const elapsed = this.lastFrameAt > 0 ? (now - this.lastFrameAt) / 1000 : 1 / PROFILE_FPS[this.profile]
        this.lastFrameAt = now
        const delta = clamp(elapsed, 0.001, MAX_FRAME_DELTA)
        this.character.update(delta)

        this.renderer.render(this.scene, this.camera)

        if (this.statsWindowStart === 0) {
          this.statsWindowStart = now
        }

        this.statsFrames++

        if (now - this.statsWindowStart >= 1000) {
          this.stats.fps = (this.statsFrames * 1000) / (now - this.statsWindowStart)
          this.statsFrames = 0
          this.statsWindowStart = now
          reportFrameStats(this.profile, this.stats.fps)
        }

        this.consecutiveErrors = 0
      }
    } catch (err) {
      // 渲染守卫——连续多次错误才停止 ticker，避免瞬态 WebGL 错误导致永久冻屏；
      // 通过 $engineError 暴露给开发者覆盖层。
      this.consecutiveErrors++
      const message = err instanceof Error ? err.message : String(err)
      log.error('engine', `ticker error (${this.consecutiveErrors}/${MAX_CONSECUTIVE_TICK_ERRORS}):`, err)

      if (this.consecutiveErrors >= MAX_CONSECUTIVE_TICK_ERRORS) {
        this.running = false
        reportEngineError(message)
        log.error('engine', 'ticker stopped after consecutive errors')

        return
      }
    } finally {
      this.isTicking = false
    }

    this.scheduleNext()
  }

  private scheduleNext(): void {
    if (this.disposed) {
      return
    }

    if (this.profile === 'dormant') {
      this.timerId = setTimeout(this.tick, DORMANT_TICK_MS)
    } else if (document.visibilityState === 'hidden') {
      this.timerId = setTimeout(this.tick, this.profile === 'active' ? HIDDEN_ACTIVE_MS : HIDDEN_IDLE_MS)
    } else if (this.profile === 'idle') {
      // 待机 30fps：按剩余时间定时唤醒，避免在高刷屏上每帧被 rAF 唤醒空转，
      // 让 CPU/GPU 能进入深度低功耗 C-states。
      const budgetMs = 1000 / PROFILE_FPS.idle
      const elapsed = performance.now() - this.lastFrameAt
      const delay = Math.max(1, Math.round(budgetMs - elapsed))
      this.timerId = setTimeout(this.tick, delay)
    } else {
      this.rafId = requestAnimationFrame(this.tick)
    }
  }

  private cancelPendingLoop(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId)
      this.rafId = null
    }

    if (this.timerId !== null) {
      clearTimeout(this.timerId)
      this.timerId = null
    }
  }

  resize(width: number, height: number): void {
    // 重新选择像素比：window.devicePixelRatio 在跨不同 DPI 显示器拖动时会变化。
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_DPR))
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(width, height, false)
    this.frameCharacter()
  }

  dispose(): void {
    this.disposed = true
    this.stop()
    this.lighting.dispose(this.scene)
    this.character.dispose()
    this.hitRT?.dispose()
    this.hitRT = null
    this.hitMap = null
    this.hitRefresh = null
    this.renderer.dispose()
    this.canvas.remove()
  }
}
