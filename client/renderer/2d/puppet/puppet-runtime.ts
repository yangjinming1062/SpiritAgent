/** Puppet WebGL 运行时 — 自 Anime2.5DRig（MIT）index.html 核心移植：
 * 每层 ArtMesh（alpha 轮廓三角剖分）+ deform() 顶点形变（头转/呼吸/眨眼差分/发束弹簧/胸物理）
 * + 模板眼裁切 + 头部三角控制笼重心绑定。形变数学与上游保持一致；GL 装配、rAF 生命周期、
 * 动画自动化层（Phase 1）、网格/绑定（Phase 2）、伪 3D 转头（Phase 3：圆投影深度曲线/
 * 远眼收窄/周边可见度/颈双隶属/上身同源跟随）与次级运动（Phase 4：发束多段弹簧链/裙双频/
 * 耳事件/呆毛/种子化自主段落，机制取自 PuppetLoom）为本仓代码。
 */

import { clamp } from '@runtime'

import { log } from '@/shared/lib/log'

import { buildArtMesh } from './artmesh'
import { buildHeadCage, cageBary, curveDepth, headBlendMu, type HeadCage } from './head-cage'
import { assessLimbTier } from './limb-split'
import type { LimbTier, Rig, RigAnchors, RigEyeAnchor, RigImage, RigPart } from './puppet-types'
import {
  type BoneId,
  buildSkeleton,
  createMat2D,
  invertMat2D,
  makeTRS,
  multiplyMat2D,
  type Skeleton,
  transformPoint
} from './skeleton'
import { buildLayerSkin, HEAD_FEATURE_BNS, type LayerSkin } from './skin'
import { ensureVendorLibs } from './vendor-loader'

interface PuppetParams {
  angleX: number
  angleY: number
  angleZ: number
  eyeOpenL: number
  eyeOpenR: number
  eyeX: number
  eyeY: number
  brow: number
  mouthOpen: number
  mouthForm: number
  mouthCY: number
  body: number
  physAmp: number
  soft: number
  browAngL: number
  browAngR: number
  browAngSym: number
  bangL: number
  bangC: number
  bangR: number
  armY: number
  armPos: number
  bust: number
  bustY: number
  irisScale: number
  mouthEase: number
  eyeEase: number
  fhAmp: number
  fhSoft: number
  eyeCY: number
  eyeCAng: number
  mouthCAng: number
  eyeScaleL: number
  eyeScaleR: number
  mouthScale: number
  // 四肢 IK 与摆臂目标（新增）
  handIKL: { x: number; y: number } | null
  handIKR: { x: number; y: number } | null
  footIKL: { dx: number; dy: number } | null
  footIKR: { dx: number; dy: number } | null
  armSwingL: number
  armSwingR: number
  suspendAngle: number
  suspendStretch: number
  suspendWeight: number
}

interface PuppetAuto {
  idle: boolean
  blink: boolean
  rand: boolean
  talk: boolean
  phys: boolean
  gaze: boolean
}

/** 调试/无头验证用：平滑后的活动参数只读快照 */
interface PuppetSnapshot {
  eyeOpenL: number
  eyeOpenR: number
  eyeX: number
  eyeY: number
  angleX: number
  angleY: number
  angleZ: number
  body: number
  mouthOpen: number
  mouthForm: number
  breath: number
  blinkActive: boolean
}

function defaultPuppetParams(): PuppetParams {
  return {
    angleX: 0,
    angleY: 0,
    angleZ: 0,
    eyeOpenL: 1,
    eyeOpenR: 1,
    eyeX: 0,
    eyeY: 0,
    brow: 0,
    mouthOpen: 0,
    mouthForm: 0,
    mouthCY: 0,
    body: 0,
    physAmp: 2,
    soft: 2,
    browAngL: 0,
    browAngR: 0,
    browAngSym: 0,
    bangL: 0,
    bangC: 0,
    bangR: 0,
    armY: 0,
    armPos: 0,
    bust: 2.5,
    bustY: 1,
    irisScale: 1,
    mouthEase: 0.45,
    eyeEase: 0.3,
    fhAmp: 2,
    fhSoft: 0.4,
    eyeCY: 0,
    eyeCAng: 0,
    mouthCAng: 0,
    eyeScaleL: 1,
    eyeScaleR: 1,
    mouthScale: 1,
    handIKL: null,
    handIKR: null,
    footIKL: null,
    footIKR: null,
    armSwingL: 0,
    armSwingR: 0,
    suspendAngle: 0,
    suspendStretch: 1,
    suspendWeight: 0
  }
}

/** 分参数平滑速率（1/s）：目光快、头慢半拍、眨眼最急 */
const PARAM_RATE: Partial<Record<keyof PuppetParams, number>> = {
  eyeX: 20,
  eyeY: 20,
  eyeOpenL: 22,
  eyeOpenR: 22,
  angleX: 7,
  angleY: 7,
  angleZ: 7,
  body: 5,
  mouthOpen: 16,
  mouthForm: 9,
  armSwingL: 14,
  armSwingR: 14
}

/** PuppetParams 字段顺序列表：tickBody 按此顺序做参数平滑。避免每帧 Object.keys 分配 38 元素字符串数组。 */
const PARAM_KEYS: readonly (keyof PuppetParams)[] = Object.freeze([
  'angleX',
  'angleY',
  'angleZ',
  'eyeOpenL',
  'eyeOpenR',
  'eyeX',
  'eyeY',
  'brow',
  'mouthOpen',
  'mouthForm',
  'mouthCY',
  'body',
  'physAmp',
  'soft',
  'browAngL',
  'browAngR',
  'browAngSym',
  'bangL',
  'bangC',
  'bangR',
  'armY',
  'armPos',
  'bust',
  'bustY',
  'irisScale',
  'mouthEase',
  'eyeEase',
  'fhAmp',
  'fhSoft',
  'eyeCY',
  'eyeCAng',
  'mouthCAng',
  'eyeScaleL',
  'eyeScaleR',
  'mouthScale',
  'handIKL',
  'handIKR',
  'footIKL',
  'footIKR',
  'armSwingL',
  'armSwingR',
  'suspendAngle',
  'suspendStretch',
  'suspendWeight'
] as (keyof PuppetParams)[])

/** handIK / footIK 在 PARAM_KEYS 中的索引（性能关键路径上的直接分流）。 */
const IK_HAND_L = PARAM_KEYS.indexOf('handIKL')
const IK_HAND_R = PARAM_KEYS.indexOf('handIKR')
const IK_FOOT_L = PARAM_KEYS.indexOf('footIKL')
const IK_FOOT_R = PARAM_KEYS.indexOf('footIKR')
const SIDES: readonly ('l' | 'r')[] = Object.freeze(['l', 'r'] as ('l' | 'r')[])

/** 吸气快、呼气慢的非对称呼吸曲线（p 为周期相位 [0,1)） */
function breathCurve(p: number): number {
  return p < 0.42 ? smooth(p / 0.42) : 1 - smooth((p - 0.42) / 0.58)
}

/** Phase 3 转头圆投影常量。角度参数 = 归一化正弦（θ = asin(a·sinθmax)）：
 * 中心位移对参数保持线性（与 Phase 2 同幅，可乘 TURN_BOOST 微增），
 * 远/近缘压缩按真实余弦（小角度趋零、满角最强），中立姿态严格保持原图。 */
const TURN_MAX = 0.55
const TURN_SIN = Math.sin(TURN_MAX)
const TURN_COMP = 1 - Math.cos(TURN_MAX)
const TURN_BOOST = 1.15
const COMP_GAIN = 0.85
const PITCH_PROF = 0.085
const FAR_EYE_NARROW = 0.16
const FAR_FADE = 0.55

/** Phase 4 次级运动常量：发束弹簧链节数（PuppetLoom 为 3-5）；裙双频；耳/呆毛事件节奏 */
const HAIR_CHAIN = 4
const SKIRT_W1 = 0.9
const SKIRT_W2 = 2.35

/** Phase 5 翻转防护：可见度轮廓的缘部保底（T 在 |hx|=1 处保留 40% 位移）。
 * 纯圆根 sqrt(1-hx²) 缘处斜率无界，与压缩项叠加会使局部映射非单调（网格折叠）——
 * T = sqrt(1-(1-m²)hx²) 斜率上界 (1-m²)/m，保证 m=0.4 时全姿态单调。 */
const RIM_KEEP = 0.4
const RIM_SLOPE = 1 - RIM_KEEP * RIM_KEEP

interface GLPart {
  name: string
  bn: string
  side: string | null
  fade: string | null
  group: 'head' | 'body'
  phys: string | null
  depth: number
  x: number
  y: number
  w: number
  h: number
  base: Float32Array
  cur: Float32Array
  nIdx: number
  /** 装配期预计算：是否头部/五官部件（不走骨骼蒙皮）；避免每帧 Set.has。 */
  isHeadPart: boolean
  /** 装配期预计算：眼部相关（eyewhite/irides/eyelash/eye_close）；避免每帧 indexOf。 */
  isEyePart: boolean
  /** 装配期预计算：原名以 eyewhite 开头；render 模板眼模板渲染。 */
  isEyewhite: boolean
  /** 装配期预计算：原名以 irides 开头。 */
  isIrides: boolean
  idx: Uint16Array
  cb: Float32Array | null
  dEff: Float32Array | null
  mu: Float32Array | null
  invHR: Float32Array | null
  sw: Float32Array | null
  su: Float32Array | null
  bw: Float32Array | null
  spr: { nodes: { x: number; v: number }[]; phase: number }[] | null
  ahoge: { y0: number; y1: number } | null
  tex: WebGLTexture
  vboPos: WebGLBuffer
  vboUV: WebGLBuffer
  ibo: WebGLBuffer
  skin?: LayerSkin | null
}

type Evaluated = PuppetParams & {
  breath: number
  breathHead: number
  simT: number
  earLift: number
  ahogeDy: number
}

/** 头部与五官部件集合在 skin.ts 集中维护（HEAD_FEATURE_BNS），deform 头部分支判别共享。 */

/** 种子化自主段落的动作环：左右观察→抬头→低头，每步之间回正 */
const SEG_CYCLE = ['r', 'n', 'l', 'n', 'u', 'n', 'd', 'n'] as const

/** 点在三角形内（含边界，符号法）——hitPart 命中检测用。 */
function triContains(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
  cx: number,
  cy: number
): boolean {
  const d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
  const d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
  const d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)

  return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0))
}

function smooth(t: number): number {
  t = clamp(t, 0, 1)

  return t * t * (3 - 2 * t)
}

export class PuppetRuntime {
  private readonly canvas: HTMLCanvasElement
  private readonly gl: WebGLRenderingContext
  private readonly prog: WebGLProgram
  private readonly locPos: number
  private readonly locUV: number
  private readonly locRes: WebGLUniformLocation | null
  private readonly locCut: WebGLUniformLocation | null
  private readonly locAlpha: WebGLUniformLocation | null

  private layers: GLPart[] = []
  private readonly interactionMatrix = createMat2D()
  private readonly inverseInteraction = createMat2D()
  private readonly suspensionMatrix = createMat2D()
  private readonly grabBind = { x: 0, y: 0 }
  private readonly grabCanvas = { x: 0, y: 0 }
  private hasGrabPoint = false
  private readonly contactTarget = { x: 0, y: 0 }

  private anchors: RigAnchors | null = null
  private headCage: HeadCage | null = null
  private tier: 'semantic' | 'grouped' | 'minimal' = 'grouped'
  private limbTier: LimbTier = 'blob'
  private skeleton: Skeleton | null = null
  private meshVerts = 0
  private meshTris = 0
  private meshArtmesh = 0
  private meshFallback = 0
  private cw = 768
  private ch = 768
  private fs = 1
  private raf = 0
  private lastNow = 0
  private simPaused = false
  private simNow = 0
  private blinkT = -1
  private blinkFloor = 0
  private nextBlink = performance.now() + 1800
  private gaze: { x: number; y: number } | null = null
  private gazeUntil = 0
  private sac = { x: 0, y: 0 }
  private nextSac = 0
  private segAt = 0
  private segDur = 0
  private segStep = -1
  private segFrom = { ax: 0, ay: 0 }
  private segTo = { ax: 0, ay: 0 }
  private rngState = 0
  private earNext = 0
  private earEvT0 = -1
  private ahogeNext = 0
  private ahogeEvT0 = -1
  private readonly ahogeS = { x: 0, v: 0 }
  private talkOn = false
  private talkV = 0
  private talkTgt = 0
  private talkF = 0
  private talkFTgt = 0
  private talkAmp = 1
  private nextTalkState = 0
  private nextSyl = 0
  private breathP = 0
  private nextSigh = performance.now() + 9000
  private sighUntil = 0
  private lastE: Evaluated | null = null
  private readonly bounce = { x: 0, v: 0, dy: 0 }
  private readonly cur: PuppetParams
  private disposed = false

  /** 外部驱动的目标参数与自动化开关；调用方直接改字段即可。 */
  readonly target: PuppetParams = defaultPuppetParams()
  readonly auto: PuppetAuto = { idle: true, blink: true, rand: true, talk: true, phys: true, gaze: true }
  /** 自主段落/耳/呆毛事件的种子（同种子同时间序列 → 相同动作）；改后调 reseed 生效 */
  autoSeed = 20260826

  /** 待机呼吸幅度缩放系数（走路 0.7，趴姿 0.4；默认 1.0） */
  idleScale = 1

  /** 冻结连续动画（呼吸相位/深呼吸调度）——姿态定格与 13 姿态验证用，保证逐位可复现。 */
  frozen = false

  onRigApplied: ((rig: Rig) => void) | null = null

  /** 视线焦点注入（归一化 [-1,1]，y 屏幕坐标向下）；传 null 回落到随机漫游。3s 无更新自动过期。 */
  setGaze(x: number | null, y = 0): void {
    this.gaze = x === null ? null : { x: clamp(x, -1.2, 1.2), y: clamp(y, -1, 1) }

    if (this.gaze) {
      this.gazeUntil = (this.simPaused ? this.simNow : performance.now()) + 3000
    }
  }

  /** 立即触发一次眨眼（静息时）；验证与 Phase 6 情绪驱动的确定性钩子。 */
  forceBlink(): void {
    if (this.blinkT < 0) {
      this.blinkT = 0
      this.blinkFloor = 0
    }
  }

  snapshot(): PuppetSnapshot {
    const e = this.lastE

    return {
      eyeOpenL: this.cur.eyeOpenL,
      eyeOpenR: this.cur.eyeOpenR,
      eyeX: this.cur.eyeX,
      eyeY: this.cur.eyeY,
      angleX: this.cur.angleX,
      angleY: this.cur.angleY,
      angleZ: this.cur.angleZ,
      body: this.cur.body,
      mouthOpen: this.cur.mouthOpen,
      mouthForm: this.cur.mouthForm,
      breath: e?.breath ?? 0,
      blinkActive: this.blinkT >= 0
    }
  }

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    const gl = canvas.getContext('webgl', { alpha: true, stencil: true, antialias: true, premultipliedAlpha: true })

    if (!gl) {
      throw new Error('WebGL unavailable')
    }

    this.gl = gl

    const sh = (type: number, src: string): WebGLShader => {
      const s = gl.createShader(type)!
      gl.shaderSource(s, src)
      gl.compileShader(s)

      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        throw new Error(gl.getShaderInfoLog(s) ?? 'shader compile failed')
      }

      return s
    }

    const prog = gl.createProgram()!
    gl.attachShader(
      prog,
      sh(
        gl.VERTEX_SHADER,
        'attribute vec2 aPos; attribute vec2 aUV; uniform vec2 uRes; varying vec2 vUV;' +
          'void main(){ vUV=aUV; vec2 c = aPos/uRes*2.0-1.0; gl_Position=vec4(c.x,-c.y,0.0,1.0); }'
      )
    )
    gl.attachShader(
      prog,
      sh(
        gl.FRAGMENT_SHADER,
        'precision mediump float; varying vec2 vUV; uniform sampler2D uTex; uniform float uCut; uniform float uAlpha;' +
          'void main(){ vec4 c=texture2D(uTex,vUV); if(c.a<uCut) discard; gl_FragColor=c*uAlpha; }'
      )
    )
    gl.linkProgram(prog)
    gl.useProgram(prog)
    this.prog = prog
    this.locPos = gl.getAttribLocation(prog, 'aPos')
    this.locUV = gl.getAttribLocation(prog, 'aUV')
    this.locRes = gl.getUniformLocation(prog, 'uRes')
    this.locCut = gl.getUniformLocation(prog, 'uCut')
    this.locAlpha = gl.getUniformLocation(prog, 'uAlpha')
    gl.enableVertexAttribArray(this.locPos)
    gl.enableVertexAttribArray(this.locUV)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true)
    this.cur = defaultPuppetParams()
    this.reseed()
    this.earNext = performance.now() + 6000
    this.ahogeNext = performance.now() + 4000
  }

  /** 重播种子：自主段落与事件调度回到该种子的确定序列。 */
  reseed(): void {
    this.rngState = this.autoSeed | 0
    this.segStep = -1
    this.segAt = 0
    this.segDur = 0
    this.segTo = { ax: 0, ay: 0 }
    this.segFrom = { ax: 0, ay: 0 }
  }

  /** mulberry32 — 事件调度专用的种子化 PRNG（无每帧无关随机数，序列可复现）。 */
  private rng(): number {
    this.rngState = (this.rngState + 0x6d2b79f5) | 0
    let t = this.rngState
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)

    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  /** 推进种子化观察段落一步：按环取目标（回正步在两个动作之间），幅度/时长带种子抖动。 */
  private advanceSeg(now: number): void {
    this.segFrom = { ...this.segTo }
    this.segStep = this.segStep < 0 ? 0 : (this.segStep + 1) % SEG_CYCLE.length
    const k = SEG_CYCLE[this.segStep]!
    let dur: number

    if (k === 'n') {
      this.segTo = { ax: 0, ay: 0 }
      dur = 0.9 + this.rng() * 0.6
    } else {
      const j = () => this.rng()

      if (k === 'r') {
        this.segTo = { ax: 0.45 + j() * 0.25, ay: (j() * 2 - 1) * 0.08 }
      } else if (k === 'l') {
        this.segTo = { ax: -(0.45 + j() * 0.25), ay: (j() * 2 - 1) * 0.08 }
      } else if (k === 'u') {
        this.segTo = { ax: (j() * 2 - 1) * 0.12, ay: 0.35 + j() * 0.2 }
      } else {
        this.segTo = { ax: (j() * 2 - 1) * 0.12, ay: -(0.3 + j() * 0.2) }
      }

      dur = 1.2 + this.rng() * 0.4
    }

    this.segAt = now
    this.segDur = dur * 1000
  }

  /** 调试/验证用：指定部件当前顶点相对基准的平均水平位移（px），供次级运动断言。 */
  layerShift(bn: string): number {
    let s = 0
    let n = 0

    for (const L of this.layers) {
      if (L.bn !== bn) {
        continue
      }

      for (let k = 0; k < L.base.length; k += 2) {
        s += Math.abs(L.cur[k]! - L.base[k]!)
        n++
      }
    }

    return n ? s / n : 0
  }

  /** 调试/验证用：发束链梢-根位移差的均值（px），直接反映次级弹簧链输出。 */
  chainSwing(bn: string): number {
    let s = 0
    let n = 0

    for (const L of this.layers) {
      if (L.bn !== bn || !L.spr) {
        continue
      }

      for (const sp of L.spr) {
        const nds = sp.nodes
        s += Math.abs(nds[nds.length - 1]!.x - nds[0]!.x)
        n++
      }
    }

    return n ? s / n : 0
  }

  /** 命中检测：rig 画布像素坐标 → 最上层可见部件的规范层名（bn），未命中返回 null。
   * 走当前帧形变后的顶点（cur/idx），与屏幕所见一致——层序即绘制序（后画在上），
   * 自尾向头首个命中的层即视觉最上层；fade 低于渲染阈值的层与渲染同步跳过。 */
  hitPart(x: number, y: number): string | null {
    const e = this.lastE

    if (!e) {
      return null
    }

    const layers = this.layers

    for (let i = layers.length - 1; i >= 0; i--) {
      const L = layers[i]!

      if (this.fadeAlpha(L, e) < 0.004) {
        continue
      }

      const cur = L.cur
      const idx = L.idx

      for (let t = 0; t < idx.length; t += 3) {
        const a = idx[t]! * 2
        const b = idx[t + 1]! * 2
        const c = idx[t + 2]! * 2

        if (triContains(x, y, cur[a]!, cur[a + 1]!, cur[b]!, cur[b + 1]!, cur[c]!, cur[c + 1]!)) {
          return L.bn
        }
      }
    }

    return null
  }

  /** 装配档位（Phase 5 三级降级）：semantic 全语义机制 / grouped 整体运动+缩幅 / minimal 仅整体呼吸与倾斜。 */
  rigTier(): 'semantic' | 'grouped' | 'minimal' {
    return this.tier
  }

  /** 四肢完整度档位：segmented (全IK+走循环) / sided (单骨摆动+简化走循环) / blob (平移降级) */
  getLimbTier(): LimbTier {
    return this.limbTier
  }

  /** 获取当前 2D 骨架 */
  getSkeleton(): Skeleton | null {
    return this.skeleton
  }

  /** 外部交互冲量（戳/摸头发等）：给全部发束链节点注入水平初速度，衰减摆动数拍。 */
  hairImpulse(mag: number): void {
    for (const L of this.layers) {
      if (!L.spr) {
        continue
      }

      for (const sp of L.spr) {
        for (let i = 0; i < sp.nodes.length; i++) {
          sp.nodes[i]!.v += mag * (0.7 + 0.3 * i)
        }
      }
    }
  }

  /** 姿态安全评估（Phase 5）：先按当前参数重算 deform，再检查全部三角形——
   * 有向面积符号翻转（网格翻转）与最大边拉伸比（相对基准网格）；byLayer 供归因、
   * flipMinA 为翻转三角形的最小基准面积（区分真实折叠与边界退化细条）。 */
  poseSafety(): { flips: number; maxStretch: number; byLayer: Record<string, number>; flipMinA: number } {
    const e = this.lastE

    if (!e) {
      return { flips: 0, maxStretch: 1, byLayer: {}, flipMinA: Infinity }
    }

    if (this.skeleton) {
      this.updateSkeleton(e)
    }

    for (const L of this.layers) {
      this.deform(L, e)
    }

    let flips = 0
    let maxStretch = 1
    let flipMinA = Infinity
    const byLayer: Record<string, number> = {}

    for (const L of this.layers) {
      const b = L.base
      const c = L.cur
      const idx = L.idx
      let lf = 0

      for (let t = 0; t < idx.length; t += 3) {
        const a = idx[t]! * 2
        const m = idx[t + 1]! * 2
        const q = idx[t + 2]! * 2
        const s0 = (b[m]! - b[a]!) * (b[q + 1]! - b[a + 1]!) - (b[q]! - b[a]!) * (b[m + 1]! - b[a + 1]!)
        const s1 = (c[m]! - c[a]!) * (c[q + 1]! - c[a + 1]!) - (c[q]! - c[a]!) * (c[m + 1]! - c[a + 1]!)

        // 基准面积 < 6px² 的边界退化细条（stride 采样近乎共线）不计：亚像素翻转不可见
        if (Math.abs(s0) > 12 && Math.abs(s1) > 0.01 && s0 > 0 !== s1 > 0) {
          flips++
          lf++
          flipMinA = Math.min(flipMinA, Math.abs(s0) / 2)
        }

        for (const [p, r] of [
          [a, m],
          [m, q],
          [q, a]
        ] as const) {
          const ex = c[r]! - c[p]!
          const ey = c[r + 1]! - c[p + 1]!
          const bx = b[r]! - b[p]!
          const by = b[r + 1]! - b[p + 1]!

          if (bx * bx + by * by > 0.25) {
            maxStretch = Math.max(maxStretch, Math.hypot(ex, ey) / Math.hypot(bx, by))
          }
        }
      }

      if (lf) {
        byLayer[L.name] = lf
      }
    }

    return { flips, maxStretch, byLayer, flipMinA }
  }

  /** PSD 语义完整度分级：缺脸/眼锚点或层数过少 → minimal；语义层与发束链齐全 → semantic。 */
  private assessTier(rig: Rig, A: RigAnchors): 'semantic' | 'grouped' | 'minimal' {
    const bns = new Set(
      rig.layers.map(l => {
        const n = l.name.toLowerCase().replace(/[-_](l|r)$/, '')

        return window.Rigger?.baseName(n) ?? n
      })
    )

    if (!bns.has('face') || rig.layers.length < 6 || (!A.eyeL && !A.eyeR)) {
      return 'minimal'
    }

    const hairStrands = rig.layers.some(l => /hair/.test(l.name) && (l.strands?.length ?? 0) > 0)
    const need = ['neck', 'front hair', 'back hair', 'topwear', 'mouth_open', 'mouth_close']

    return need.every(n => bns.has(n)) && A.eyeL && A.eyeR && hairStrands ? 'semantic' : 'grouped'
  }

  /** see-through 产出 `-l/-r` 后缀层名，绕过 vendor SLOTS 匹配（side/fade/眼锚点缺失，
   * 虹膜/眉毛/远眼收窄/耳淡出全部失效）——在装配边界补齐眼锚点，side/fade 在 buildGlPart 补。 */
  private patchSideParts(rig: Rig, A: RigAnchors): void {
    const find = (bn: string, side: string): RigPart | undefined =>
      rig.layers.find(l => {
        const n = l.name.toLowerCase()

        return n === `${bn}_${side}` || n === `${bn}-${side}`
      })

    const bbox = (p: RigPart): { x0: number; y0: number; x1: number; y1: number } | null => {
      const { width: w, height: h, data } = p.img
      let x0 = w
      let y0 = h
      let x1 = -1
      let y1 = -1

      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          if (data[(y * w + x) * 4 + 3]! > 8) {
            if (x < x0) {
              x0 = x
            }

            if (x > x1) {
              x1 = x
            }

            if (y < y0) {
              y0 = y
            }

            if (y > y1) {
              y1 = y
            }
          }
        }
      }

      return x1 < 0 ? null : { x0: p.x + x0, y0: p.y + y0, x1: p.x + x1, y1: p.y + y1 }
    }

    const centroid = (p: RigPart): { x: number; y: number } | null => {
      const { width: w, height: h, data } = p.img
      let sx = 0
      let sy = 0
      let n = 0

      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          if (data[(y * w + x) * 4 + 3]! > 8) {
            sx += x
            sy += y
            n++
          }
        }
      }

      return n ? { x: p.x + sx / n, y: p.y + sy / n } : null
    }

    for (const side of ['l', 'r'] as const) {
      const key = side === 'l' ? 'eyeL' : 'eyeR'

      if (A[key]) {
        continue
      }

      const ew = find('eyewhite', side)

      if (!ew) {
        continue
      }

      const b = bbox(ew)

      if (!b) {
        continue
      }

      const ic = centroid(find('irides', side) ?? ew)!
      const ecc = find('eye_close', side)
      const cc = ecc ? centroid(ecc) : null // 合成闭眼层质心即睑线近似
      A[key] = {
        x0: b.x0,
        x1: b.x1,
        y0: b.y0,
        y1: b.y1,
        icx: ic.x,
        icy: ic.y,
        closeY: cc ? cc.y : b.y0 + (b.y1 - b.y0) * 0.62
      }
    }
  }

  applyRig(rig: Rig): void {
    if (this.disposed) {
      return
    }

    const gl = this.gl

    for (const L of this.layers) {
      gl.deleteTexture(L.tex)
      gl.deleteBuffer(L.vboPos)
      gl.deleteBuffer(L.vboUV)
      gl.deleteBuffer(L.ibo)
    }

    this.layers = []
    this.cw = rig.canvas.w
    this.ch = rig.canvas.h
    const A = rig.anchors
    this.anchors = A
    this.fs = A.faceScale
    this.patchSideParts(rig, A)

    // Phase 2: 头部双表面控制笼（dF=脸层深度，dS=头部层最大深度≈头骨）；
    // Phase 3: 头骨横向半径按头部组层外包矩形实测（毛发/耳一般比脸缘宽）
    let dF = 1
    let dS = 2
    let rsMin = Infinity
    let rsMax = -Infinity

    for (const L of rig.layers) {
      if (L.group !== 'head') {
        continue
      }

      dS = Math.max(dS, L.depth)
      rsMin = Math.min(rsMin, L.x)
      rsMax = Math.max(rsMax, L.x + L.w)

      if (window.Rigger?.baseName(L.name.replace(/_(l|r)$/, '')) === 'face') {
        dF = L.depth
      }
    }

    const rs = rsMax > rsMin ? Math.max(Math.abs(rsMin - A.face.cx), Math.abs(rsMax - A.face.cx)) : 0
    this.headCage = buildHeadCage(A, dF, dS, rs)
    this.skeleton = buildSkeleton(rig)
    this.limbTier = assessLimbTier(rig)
    // Phase 5: PSD 语义完整度分级（semantic→grouped→minimal），门控机制与动作幅度
    this.tier = this.assessTier(rig, A)
    this.meshVerts = 0
    this.meshTris = 0
    this.meshArtmesh = 0
    this.meshFallback = 0

    for (const Lr of rig.layers) {
      const L = this.buildGlPart(Lr)
      this.layers.push(L)
    }

    this.hasGrabPoint = false
    this.canvas.width = this.cw
    this.canvas.height = this.ch
    this.onRigApplied?.(rig)
  }

  private buildGlPart(Lr: RigPart): GLPart {
    const gl = this.gl
    const A = this.anchors!
    const isLimb = /^(handwear|arm|hand|legwear|leg|footwear|foot)(?:[_ -]|$)/i.test(Lr.name)
    const cell = (isLimb ? 14 : Lr.phys ? 30 : 42) * Math.max(0.6, this.cw / 768)

    // Phase 2: alpha 轮廓 ArtMesh；退化/空层回退 2×2 quad
    const am = buildArtMesh(Lr.img, cell)
    let nv: number
    let base: Float32Array
    let uv: Float32Array
    let idx: Uint16Array

    if (am) {
      nv = am.verts.length / 2
      base = new Float32Array(nv * 2)
      uv = new Float32Array(nv * 2)

      for (let v = 0; v < nv; v++) {
        const vx = am.verts[v * 2]!
        const vy = am.verts[v * 2 + 1]!
        base[v * 2] = Lr.x + vx
        base[v * 2 + 1] = Lr.y + vy
        uv[v * 2] = vx / Lr.w
        uv[v * 2 + 1] = vy / Lr.h
      }

      idx = am.tris
      this.meshVerts += nv
      this.meshTris += am.stats.tris
      this.meshArtmesh++
    } else {
      nv = 4
      base = new Float32Array([Lr.x, Lr.y, Lr.x + Lr.w, Lr.y, Lr.x, Lr.y + Lr.h, Lr.x + Lr.w, Lr.y + Lr.h])
      uv = new Float32Array([0, 0, 1, 0, 0, 1, 1, 1])
      idx = new Uint16Array([0, 1, 2, 1, 3, 2])
      this.meshVerts += 4
      this.meshTris += 2
      this.meshFallback++
    }

    // see-through 的 `-l/-r` 后缀绕过 vendor SLOTS：规范 bn/side，并给开眼层补 fade
    const bnRaw = Lr.name.replace(/[-_](l|r)$/, '')
    const bn = window.Rigger?.baseName(bnRaw) ?? bnRaw
    const mSide = /[-_](l|r)$/.exec(Lr.name)
    const side = Lr.side ?? (mSide ? mSide[1]!.toUpperCase() : null)
    let fade = Lr.fade

    if (!fade) {
      if (bn === 'eyewhite' || bn === 'irides' || bn === 'eyelash') {
        fade = 'eyeOpen'
      } else if (bn === 'eye_close') {
        fade = 'eyeClose'
      }
    }

    let sw: Float32Array | null = null
    let su: Float32Array | null = null
    let bw: Float32Array | null = null
    let spr: GLPart['spr'] = null
    const S = Lr.strands

    if (S && S.length) {
      const nS = S.length
      let spacing = 120

      if (nS > 1) {
        const ds: number[] = []

        for (let s = 1; s < nS; s++) {
          ds.push(S[s]!.x - S[s - 1]!.x)
        }

        ds.sort((a, b) => a - b)
        spacing = ds[ds.length >> 1] ?? spacing
      }

      const sig = spacing * 0.6
      sw = new Float32Array(nv * nS)
      su = new Float32Array(nv)
      spr = S.map((_s, i) => ({
        nodes: Array.from({ length: HAIR_CHAIN }, () => ({ x: 0, v: 0 })),
        phase: i * 1.37 + Lr.z
      }))

      for (let v = 0; v < nv; v++) {
        const x = base[v * 2]!
        const y = base[v * 2 + 1]!
        let tot = 0

        for (let s = 0; s < nS; s++) {
          const w = Math.exp(-(((x - S[s]!.x) / sig) ** 2))
          sw[v * nS + s] = w
          tot += w
        }

        let rY = 0
        let tY = 0

        if (tot > 1e-6) {
          for (let s = 0; s < nS; s++) {
            sw[v * nS + s] = sw[v * nS + s]! / tot
            rY += sw[v * nS + s]! * S[s]!.rootY
            tY += sw[v * nS + s]! * S[s]!.tipY
          }
        } else {
          sw[v * nS] = 1
          rY = S[0]!.rootY
          tY = S[0]!.tipY
        }

        su[v] = clamp((y - rY) / Math.max(1, tY - rY), 0, 1)
      }

      if (bn === 'front hair') {
        const fw = A.face.x1 - A.face.x0
        const fcx = A.face.cx
        const f = 36
        const b1 = fcx - fw * 0.22
        const b2 = fcx + fw * 0.22
        bw = new Float32Array(nv * 3)

        for (let v = 0; v < nv; v++) {
          const x = base[v * 2]!
          const s1 = smooth((x - b1) / f + 0.5)
          const s2 = smooth((x - b2) / f + 0.5)
          bw[v * 3] = 1 - s1
          bw[v * 3 + 1] = s1 * (1 - s2)
          bw[v * 3 + 2] = s2
        }
      }
    }

    // Phase 2: 控制笼绑定 — 重心坐标 + 脸面↔头骨混合 dEff（前发根随脸、梢随颅的连续深度过渡）；
    // Phase 3: 逐顶点 μ 与混合表面横向半径（μ=1 脸面 Rf → μ=0 头骨 Rs）供圆投影用
    const cage = this.headCage
    let cb: Float32Array | null = null
    let dEff: Float32Array | null = null
    let mu: Float32Array | null = null
    let invHR: Float32Array | null = null

    if (cage) {
      cb = new Float32Array(nv * 3)
      dEff = new Float32Array(nv)
      mu = new Float32Array(nv)
      invHR = new Float32Array(nv)
      const muBase = headBlendMu(cage.dF, cage.dS, Lr.depth)
      const dr = cage.rs - cage.rf

      for (let v = 0; v < nv; v++) {
        cageBary(cage, base[v * 2]!, base[v * 2 + 1]!, cb, v * 3)

        let m = muBase

        if (bn === 'front hair' && su) {
          m = clamp(muBase + 0.15 - 0.3 * su[v]!, 0, 1)
        }

        mu[v] = m
        dEff[v] = cage.dS + m * (cage.dF - cage.dS)
        invHR[v] = 1 / Math.max(1, cage.rf + (1 - m) * dr)
      }
    }

    // 呆毛检测（Phase 4）：前发顶部窄于主发宽 35% 的连续突出段为呆毛，梢部挂纵向弹动
    let ahoge: { y0: number; y1: number } | null = null

    if (bn === 'front hair') {
      const iw = Lr.img.width
      const ih = Lr.img.height
      const dta = Lr.img.data

      const rowW = (y: number): number => {
        let c = 0

        for (let x = 0; x < iw; x++) {
          if (dta[(y * iw + x) * 4 + 3]! > 12) {
            c++
          }
        }

        return c
      }

      const ref = rowW(Math.round(ih * 0.3))
      let top = -1
      let bot = -1

      for (let y = 1; y < ih * 0.3; y += 2) {
        const rw = rowW(y)

        if (rw > 2 && rw < ref * 0.35) {
          if (top < 0) {
            top = y
          }

          bot = y
        } else if (top >= 0) {
          break
        }
      }

      if (top >= 0 && bot - top >= 5) {
        ahoge = { y0: Lr.y + top, y1: Lr.y + bot }
      }
    }

    const vboPos = gl.createBuffer()!
    const vboUV = gl.createBuffer()!
    const ibo = gl.createBuffer()!
    gl.bindBuffer(gl.ARRAY_BUFFER, vboUV)
    gl.bufferData(gl.ARRAY_BUFFER, uv, gl.STATIC_DRAW)
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo)
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW)

    const tex = gl.createTexture()!
    gl.bindTexture(gl.TEXTURE_2D, tex)
    const idata = new ImageData(new Uint8ClampedArray(Lr.img.data), Lr.img.width, Lr.img.height)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, idata)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)

    const part: GLPart = {
      name: Lr.name,
      bn,
      side,
      fade,
      group: Lr.group,
      phys: Lr.phys,
      depth: Lr.depth,
      x: Lr.x,
      y: Lr.y,
      w: Lr.w,
      h: Lr.h,
      base,
      cur: new Float32Array(base),
      nIdx: idx.length,
      idx,
      isHeadPart: Lr.group === 'head' || HEAD_FEATURE_BNS.has(bn),
      isEyePart: bn === 'eyewhite' || bn === 'irides' || bn === 'eyelash' || bn === 'eye_close',
      isEyewhite: Lr.name.indexOf('eyewhite') === 0,
      isIrides: Lr.name.indexOf('irides') === 0,
      cb,
      dEff,
      mu,
      invHR,
      sw,
      su,
      bw,
      spr,
      ahoge,
      tex,
      vboPos,
      vboUV,
      ibo
    }

    if (this.skeleton && A) {
      part.skin = buildLayerSkin(part, this.skeleton, A)
    }

    return part
  }

  start(): void {
    if (this.raf) {
      return
    }

    this.lastNow = performance.now()

    const loop = (now: number): void => {
      if (this.disposed) {
        return
      }

      this.raf = requestAnimationFrame(loop)

      if (this.simPaused) {
        this.render()
      } else {
        this.tickBody(now)
      }
    }

    this.raf = requestAnimationFrame(loop)
  }

  dispose(): void {
    this.disposed = true
    cancelAnimationFrame(this.raf)
    const gl = this.gl

    for (const L of this.layers) {
      gl.deleteTexture(L.tex)
      gl.deleteBuffer(L.vboPos)
      gl.deleteBuffer(L.vboUV)
      gl.deleteBuffer(L.ibo)
    }

    this.layers = []
    gl.deleteProgram(this.prog)
  }

  get size(): { w: number; h: number } {
    return { w: this.cw, h: this.ch }
  }

  /** 网格统计（无头断言用）：ArtMesh 层数 / 回退层数 / 总顶点 / 总三角形。 */
  meshStats(): { layers: number; verts: number; tris: number; artmesh: number; fallback: number } {
    return {
      layers: this.layers.length,
      verts: this.meshVerts,
      tris: this.meshTris,
      artmesh: this.meshArtmesh,
      fallback: this.meshFallback
    }
  }

  private fadeAlpha(L: GLPart, e: Evaluated): number {
    let a = 1

    if (L.fade === 'eyeOpen') {
      const v = L.side === 'L' ? e.eyeOpenL : e.eyeOpenR
      a = smooth((v - (0.1 + e.eyeEase * 0.45)) / 0.15)
    } else if (L.fade === 'eyeClose') {
      const v = L.side === 'L' ? e.eyeOpenL : e.eyeOpenR
      a = 1 - smooth((v - (0.1 + e.eyeEase * 0.45)) / 0.15)
    } else if (L.fade === 'mouthOpen') {
      a = smooth((e.mouthOpen - (0.05 + e.mouthEase * 0.35)) / 0.12)
    } else if (L.fade === 'mouthClose') {
      a = 1 - smooth((e.mouthOpen - (0.05 + e.mouthEase * 0.35)) / 0.12)
    }

    // 周边可见度（Phase 3）：远端侧挂件（耳等；眼/眉只做几何透视，不淡化）随转角淡出
    if (L.side && !L.fade && L.bn !== 'eyebrow') {
      const far = e.angleX * (L.side === 'L' ? 1 : -1)

      if (far > 0) {
        a *= 1 - FAR_FADE * Math.min(1, far)
      }
    }

    return a
  }

  setGrabPoint(x: number, y: number): void {
    this.grabCanvas.x = x
    this.grabCanvas.y = y
    invertMat2D(this.inverseInteraction, this.interactionMatrix)
    transformPoint(this.inverseInteraction, x, y, this.grabBind)
    this.hasGrabPoint = true
  }

  private updateInteraction(e: Evaluated): void {
    const m = this.interactionMatrix
    makeTRS(m, 0, 0, 0)
    const pivot = this.hasGrabPoint ? this.grabBind : { x: this.anchors!.neckPivot.cx, y: this.anchors!.neckPivot.cy }
    const suspend = this.suspensionMatrix
    makeTRS(suspend, 0, 0, e.suspendAngle, 1, e.suspendStretch)
    suspend[4] = pivot.x - suspend[0]! * pivot.x - suspend[2]! * pivot.y
    suspend[5] = pivot.y - suspend[1]! * pivot.x - suspend[3]! * pivot.y
    multiplyMat2D(m, suspend, m)

    if (this.hasGrabPoint && e.suspendWeight > 0) {
      m[4] += (this.grabCanvas.x - (m[0]! * pivot.x + m[2]! * pivot.y + m[4]!)) * e.suspendWeight
      m[5] += (this.grabCanvas.y - (m[1]! * pivot.x + m[3]! * pivot.y + m[5]!)) * e.suspendWeight
    }

    invertMat2D(this.inverseInteraction, m)
  }

  private updateSkeleton(e: Evaluated): void {
    const skel = this.skeleton

    if (!skel || !this.anchors) {
      return
    }

    this.updateInteraction(e)

    // limbTier=blob 时四肢不挂骨骼（PSD 未拆段、皮肤未绑定到四肢体段上），
    // 但仍走脊柱/肩/呼吸等躯干通道；与 tier（grouped/minimal）正交。
    const isBlob = this.limbTier === 'blob'
    const tierScale = this.tier === 'grouped' ? 0.6 : 1.0
    const fs = this.fs

    // 1. 脊柱与躯干
    const spine = skel.getBone('spine')
    const neck = skel.getBone('neck')
    const hip = skel.getBone('hip')

    if (hip) {
      hip.localAngle = e.body * 0.02 * tierScale
      hip.localOffset.x = e.body * 3 * fs * tierScale
      hip.localOffset.y = -e.breath * 1.0 * fs
    }

    if (spine) {
      spine.localAngle = e.body * 0.03 * tierScale + e.angleZ * 0.03 * tierScale
      spine.localOffset.x = e.body * 3 * fs * tierScale
      spine.localOffset.y = -e.breath * 1.5 * fs
    }

    if (neck) {
      neck.localAngle = e.angleZ * 0.04 * tierScale
    }

    // 2. 肩部呼吸微动
    const shL = skel.getBone('shoulderL')
    const shR = skel.getBone('shoulderR')

    if (shL) {
      shL.localOffset.y = -e.breath * 0.8 * fs
    }

    if (shR) {
      shR.localOffset.y = -e.breath * 0.8 * fs
    }

    if (isBlob) {
      // blob 档：不写四肢骨，回退到当前帧绑定姿态
      skel.updateMatrices()

      return
    }

    // 2.5 重置四肢骨到绑定姿态（IK 从这里解出的是「相对绑定的绝对旋转」，
    //     否则会读到上一帧的 localAngle 并叠加成相对增量 → 帧间 ping-pong）。
    //     torso 骨已经在上面写过，保留它们的 localAngle/localOffset。
    for (const b of skel.bones) {
      const id = b.id

      if (
        id === 'hip' ||
        id === 'spine' ||
        id === 'neck' ||
        id === 'head' ||
        id === 'shoulderL' ||
        id === 'shoulderR'
      ) {
        continue
      }

      b.localAngle = 0
      b.localOffset.x = 0
      b.localOffset.y = 0
    }

    // 把四肢骨 reset 后立即 updateMatrices，让下面 applyLimbIK 读到的 worldMatrix
    // 反映「当前 torso + 四肢绑定姿态」，IK 求解出的 delta0 就是绝对角。
    skel.updateMatrices()

    // 3. 臂/腿驱动：按 L/R 迭代。手势 IK > 走循环摆臂 > 绑定姿态；
    //    foot IK 始终写（由 PuppetStage 提供；null 即回退）。
    for (let i = 0; i < SIDES.length; i++) {
      const side = SIDES[i]!
      const isL = side === 'l'
      const upperId: BoneId = isL ? 'upperArmL' : 'upperArmR'
      const lowerId: BoneId = isL ? 'lowerArmL' : 'lowerArmR'

      const upper = skel.getBone(upperId)
      const lower = skel.getBone(lowerId)
      const target = isL ? e.handIKL : e.handIKR

      if (target) {
        skel.applyLimbIK('arm', side, target)
      } else {
        const swing = isL ? e.armSwingL : e.armSwingR
        const end = skel.getBone(isL ? 'handL' : 'handR')

        if (upper) {
          upper.localAngle = Math.abs(swing) > 0.001 ? swing : 0
        }

        if (lower) {
          // 双臂对称：swing 不论正负都让肘轻微前折，避免一臂僵直。
          lower.localAngle = Math.abs(swing) > 0.001 ? -Math.abs(swing) * 0.3 : 0
        }

        // 上一帧 IK 写入的 hand.localAngle（腕部 30% 跟随肘）必须清零，
        // 否则脱手后腕部永久残留一个旋转角。
        if (end) {
          end.localAngle = 0
        }
      }

      const footTarget = isL ? e.footIKL : e.footIKR

      if (footTarget) {
        const foot = skel.getBone(isL ? 'footL' : 'footR')

        if (foot) {
          const tgt = this.legTargetScratch
          tgt.x = foot.bindWorldPos.x + footTarget.dx
          tgt.y = foot.bindWorldPos.y + footTarget.dy
          skel.applyLimbIK('leg', side, tgt)
        }
      } else {
        const upperLeg = skel.getBone(isL ? 'upperLegL' : 'upperLegR')
        const lowerLeg = skel.getBone(isL ? 'lowerLegL' : 'lowerLegR')
        const foot = skel.getBone(isL ? 'footL' : 'footR')

        if (upperLeg) {
          upperLeg.localAngle = 0
        }

        if (lowerLeg) {
          lowerLeg.localAngle = 0
        }

        if (foot) {
          foot.localAngle = 0
        }
      }
    }

    skel.updateMatrices()
  }

  private deform(L: GLPart, e: Evaluated): void {
    const A = this.anchors

    if (!A) {
      return
    }

    const b = L.base
    const o = L.cur
    const n = b.length
    const bn = L.bn
    const isHeadPart = L.isHeadPart

    if (isHeadPart) {
      const az = e.angleZ * 0.07
      const cz = Math.cos(az)
      const sz = Math.sin(az)
      const ab = e.body * 0.028
      const cb = Math.cos(ab)
      const sb = Math.sin(ab)
      const NP = A.neckPivot
      const BP = A.bodyPivot
      const fcx = A.face.cx
      const fcy = A.face.cy
      const CAGE = this.headCage
      const eyeSide = L.side
      const EA: RigEyeAnchor | null = eyeSide === 'L' ? (A.eyeL ?? null) : eyeSide === 'R' ? (A.eyeR ?? null) : null
      const vOpen = eyeSide === 'L' ? e.eyeOpenL : e.eyeOpenR
      const mo = e.mouthOpen
      const mHalfW = (A.mouth.x1 - A.mouth.x0) / 2
      const nS = L.spr ? L.spr.length : 0
      const bcx = L.x + L.w / 2
      const bcy = L.y + L.h / 2
      const isFH = bn === 'front hair'
      const isEyePart = L.isEyePart
      const farEye = this.tier === 'semantic' && EA ? Math.max(0, e.angleX * (eyeSide === 'L' ? 1 : -1)) : 0

      // Phase 5 档位门控：grouped 档整体缩幅（动作缩放阶梯的安全侧近似），minimal 见循环内早退
      const turnScale = this.tier === 'grouped' ? 0.75 : 1

      // Phase 3 转角几何（每层一次）：参数即归一化正弦 → θ = asin(a·sinθmax)；
      // cX = 远/近缘压缩像素系数（满角 = (1-cosθmax)·COMP_GAIN），cY = 俯仰纵向轮廓增益 [0,1]
      const cX = (1 - Math.cos(Math.asin(clamp(e.angleX, -1, 1) * TURN_SIN))) * COMP_GAIN
      const cY = (1 - Math.cos(Math.asin(clamp(e.angleY, -1, 1) * TURN_SIN))) / TURN_COMP

      // 每层一次：把 trig 提到循环外，避免每顶点 cos/sin 重复计算。
      const eyeSideSign = eyeSide === 'L' ? 1 : -1
      const thE = e.eyeCAng * 0.3 * eyeSideSign
      const ctE = Math.cos(thE)
      const stE = Math.sin(thE)
      const th = (eyeSide === 'L' ? e.browAngL + e.browAngSym : e.browAngR - e.browAngSym) * 0.3
      const ctB = Math.cos(th)
      const stB = Math.sin(th)
      const thM = e.mouthCAng * 0.35
      const ctM = Math.cos(thM)
      const stM = Math.sin(thM)

      for (let k = 0; k < n; k += 2) {
        let x = b[k]!
        let y = b[k + 1]!
        const vi = k >> 1

        // minimal 档（Phase 5）：不做脸/肢体局部变形，只保留整体呼吸、重心横移与轻微倾斜
        if (this.tier === 'minimal') {
          y -= e.breath * 2.2 * this.fs
          x += e.body * 5 * this.fs
          o[k] = x
          o[k + 1] = y

          continue
        }

        // 远眼收窄（Phase 3）：转向时对侧眼向眼心水平压缩 — 纯几何透视，不动透明度
        if (farEye > 0 && isEyePart) {
          const cxE = (EA!.x0 + EA!.x1) / 2
          x = cxE + (x - cxE) * (1 - FAR_EYE_NARROW * farEye)
        }

        if (EA && bn === 'eye_close') {
          const sE = eyeSide === 'L' ? e.eyeScaleL : e.eyeScaleR

          if (sE !== 1) {
            const cxE = (EA.x0 + EA.x1) / 2
            const cyE = (EA.y0 + EA.y1) / 2
            x = cxE + (x - cxE) * sE
            y = cyE + (y - cyE) * sE
          }
        }

        if (bn === 'mouth_open' || bn === 'mouth_close') {
          const sM = e.mouthScale

          if (sM !== 1) {
            x = A.mouth.cx + (x - A.mouth.cx) * sM
            y = A.mouth.cy + (y - A.mouth.cy) * sM
          }
        }

        if (L.fade === 'eyeOpen' && EA) {
          if (bn === 'irides') {
            const isc = e.irisScale
            x = EA.icx + (x - EA.icx) * isc
            y = EA.icy + (y - EA.icy) * isc
            x += e.eyeX * 11 * this.fs
            y += e.eyeY * 6 * this.fs
            const tl = smooth((0.32 - vOpen) / 0.32)
            y = EA.closeY + (y - EA.closeY) * (1 - 0.8 * tl)
          } else {
            y = EA.closeY + (y - EA.closeY) * (1 - 0.85 * (1 - vOpen))
          }
        }

        if (L.fade === 'eyeClose' && EA) {
          y -= vOpen * 3
          y += e.eyeCY * 14 * this.fs

          if (thE) {
            const rx = x - bcx
            const ry = y - bcy
            x = bcx + rx * ctE - ry * stE
            y = bcy + rx * stE + ry * ctE
          }
        }

        if (bn === 'eyebrow') {
          y += (-e.brow * 9 + (1 - vOpen) * 3.5) * this.fs

          if (th) {
            const rx = x - bcx
            const ry = y - bcy
            x = bcx + rx * ctB - ry * stB
            y = bcy + rx * stB + ry * ctB
          }
        }

        if (L.fade === 'mouthOpen') {
          y = A.mouth.y0 + (y - A.mouth.y0) * (0.5 + 0.5 * mo)
          const q = (Math.abs(x - A.mouth.cx) / (mHalfW + 4)) ** 1.5
          y -= e.mouthForm * 6 * this.fs * (q - 0.35)
        }

        if (L.fade === 'mouthClose') {
          y += e.mouthCY * 14 * this.fs

          if (thM) {
            const rx = x - A.mouth.cx
            const ry = y - A.mouth.cy
            x = A.mouth.cx + rx * ctM - ry * stM
            y = A.mouth.cy + rx * stM + ry * ctM
          }
        }

        if (bn === 'face' && y > A.mouth.cy) {
          y += mo * 6 * this.fs * smooth((y - A.mouth.cy) / (A.face.y1 - A.mouth.cy))
        }

        let hw = L.group === 'head' ? 1 : L.group === 'body' ? 0.16 : 0

        // 颈双隶属（Phase 3）：上端完整跟头，下端跟衣领
        if (bn === 'neck') {
          hw = 0.12 + 0.73 * smooth((A.neckBottom - y) / Math.max(1, A.neckBottom - A.neckTop))
        }

        if (hw > 0) {
          const rx = x - NP.cx
          const ry = y - NP.cy
          const rx2 = rx * cz - ry * sz
          const ry2 = rx * sz + ry * cz
          x += (rx2 - rx) * hw
          y += (ry2 - ry) * hw

          const dd0 = L.dEff ? L.dEff[vi]! : L.depth
          const muV = L.mu ? L.mu[vi]! : 0
          const dd = dd0 + (CAGE ? muV * curveDepth(CAGE, y) : 0)
          const axF = e.angleX * this.fs * turnScale
          const ayF = e.angleY * this.fs * turnScale
          const kk = 14 + 40 * (dd - 1)
          const kl = 9 + 30 * (dd - 1)
          const ks = (dd - 1) * 0.05
          let dx: number
          let dy: number

          if (L.cb && CAGE) {
            const w0 = L.cb[vi * 3]!
            const w1 = L.cb[vi * 3 + 1]!
            const w2 = L.cb[vi * 3 + 2]!
            const py0 = CAGE.py[0]!
            const py1 = CAGE.py[1]!
            const py2 = CAGE.py[2]!
            const pb = w0 * (NP.cy - py0) + w1 * (NP.cy - py1) + w2 * (NP.cy - py2)
            const qb = w0 * (py0 - fcy) + w1 * (py1 - fcy) + w2 * (py2 - fcy)

            if (L.invHR && (L.group === 'head' || bn === 'neck')) {
              const hx = (x - fcx) * L.invHR[vi]!
              const t = Math.sqrt(Math.max(RIM_KEEP * RIM_KEEP, 1 - RIM_SLOPE * hx * hx))
              const hy = (y - fcy) / CAGE.rv
              const tp = Math.sqrt(Math.max(RIM_KEEP * RIM_KEEP, 1 - RIM_SLOPE * hy * hy))
              dx = axF * (kk * TURN_BOOST * t + 0.028 * pb) - cX * (x - fcx)
              dy = -ayF * (kl * tp + (ks + PITCH_PROF * cY) * qb)
            } else {
              dx = axF * (kk + 0.028 * pb)
              dy = -ayF * (kl + ks * qb)
            }
          } else {
            dx = axF * (kk + 0.028 * (NP.cy - y))
            dy = -ayF * (kl + ks * (y - fcy))
          }

          x += hw * dx
          y += hw * dy
        }

        y -= (L.group === 'body' ? e.breath * 2.0 : e.breathHead * 1.6) * this.fs

        // 耳事件抬落
        if ((bn === 'ears' || bn === 'earwear') && e.earLift > 0) {
          y -= e.earLift * smooth((L.y + L.h - y) / Math.max(1, L.h))
        }

        // 呆毛纵向弹动
        if (L.ahoge && e.ahogeDy !== 0) {
          y += e.ahogeDy * smooth((L.ahoge.y1 - y) / Math.max(1, L.ahoge.y1 - L.ahoge.y0)) * 1.4
        }

        if (L.bw && L.su) {
          const m = L.su[vi]! ** 1.4 * 22 * this.fs
          x += (e.bangL * L.bw[vi * 3]! + e.bangC * L.bw[vi * 3 + 1]! + e.bangR * L.bw[vi * 3 + 2]!) * m
        }

        if (nS && this.auto.phys && L.spr && L.sw && L.su) {
          const u = isFH ? Math.min(1, L.su[vi]! * 1.6) : L.su[vi]!
          const amp = u ** (isFH ? 1.8 : 2.1) * (isFH ? e.fhAmp : e.physAmp)
          let dx = 0

          for (let s = 0; s < nS; s++) {
            const w = L.sw[vi * nS + s]!

            if (w < 0.001) {
              continue
            }

            const nds = L.spr[s]!.nodes
            const cu = u * (nds.length - 1)
            const i0 = Math.min(nds.length - 2, Math.floor(cu))
            const fr = cu - i0
            dx += w * ((nds[i0]!.x - nds[0]!.x) * (1 - fr) + (nds[i0 + 1]!.x - nds[0]!.x) * fr) * 2.2
          }

          x += dx * amp
          y += Math.abs(dx) * amp * 0.12
        }

        o[k] = x
        o[k + 1] = y
      }

      if (Math.abs(ab) > 1e-4) {
        for (let k = 0; k < n; k += 2) {
          const rx = o[k]! - BP.cx
          const ry = o[k + 1]! - BP.cy
          o[k] = BP.cx + rx * cb - ry * sb
          o[k + 1] = BP.cy + rx * sb + ry * cb
        }
      }
    } else {
      // 身体骨骼蒙皮分支（Phase 6 四肢骨骼）
      const skin = L.skin
      const skel = this.skeleton
      const bones = skel ? skel.bones : null
      const boneIndices = skin ? skin.boneIndices : null
      const weights = skin ? skin.weights : null

      const isBottomwear = bn === 'bottomwear'
      const isTopwear = bn === 'topwear'
      const CHEST = isTopwear ? this.chest() : null

      // 裙摆双频相位提到循环外，每帧每层只算两次 sin
      const skirtDx =
        isBottomwear && this.auto.phys
          ? Math.sin(e.simT * SKIRT_W1 + 1.3) * 2.6 + Math.sin(e.simT * SKIRT_W2 + 4.1) * 1.4
          : 0

      for (let k = 0; k < n; k += 2) {
        let x = b[k]!
        let y = b[k + 1]!

        if (this.tier === 'minimal') {
          y -= e.breath * 2.2 * this.fs
          x += e.body * 5 * this.fs
          o[k] = x
          o[k + 1] = y

          continue
        }

        if (skin && bones && boneIndices && weights) {
          const b0 = boneIndices[k]!
          const b1 = boneIndices[k + 1]!
          const w1 = weights[k + 1]!

          const w0 = 1 - w1

          const m0 = bones[b0]!.skinMatrix
          const m1 = bones[b1]!.skinMatrix

          const x0 = m0[0]! * x + m0[2]! * y + m0[4]!
          const y0 = m0[1]! * x + m0[3]! * y + m0[5]!

          const x1 = m1[0]! * x + m1[2]! * y + m1[4]!
          const y1 = m1[1]! * x + m1[3]! * y + m1[5]!

          x = w0 * x0 + w1 * x1
          y = w0 * y0 + w1 * y1
        } else {
          y -= e.breath * 2.0 * this.fs
          x += e.body * 4 * this.fs
        }

        // 裙摆二次运动（髋骨空间双频摆）
        if (skirtDx !== 0) {
          const ww = smooth((b[k + 1]! - (L.y + L.h * 0.12)) / Math.max(1, L.h * 0.88))
          x += skirtDx * this.fs * ww
        }

        // 上衣胸部起伏
        if (isTopwear && CHEST && e.bust > 0) {
          const gx = (b[k]! - CHEST.cx) / CHEST.rx
          const gy = (b[k + 1]! - (CHEST.cy + e.bustY * 70 * this.fs)) / CHEST.ry
          y += this.bounce.dy * e.bust * Math.exp(-gx * gx - gy * gy)
        }

        o[k] = x
        o[k + 1] = y
      }
    }

    const m = this.interactionMatrix

    for (let k = 0; k < n; k += 2) {
      const x = o[k]!
      const y = o[k + 1]!
      o[k] = m[0]! * x + m[2]! * y + m[4]!
      o[k + 1] = m[1]! * x + m[3]! * y + m[5]!
    }
  }

  private chest(): { cx: number; cy: number; rx: number; ry: number } {
    const A = this.anchors!
    const NP = A.neckPivot

    return {
      cx: NP.cx,
      cy: A.neckBottom + (A.face.y1 - A.face.y0) * 0.6,
      rx: (A.face.x1 - A.face.x0) * 0.6,
      ry: (A.face.y1 - A.face.y0) * 0.45
    }
  }

  renderFrame(seconds: number): void {
    this.advanceSim(seconds)
    this.render()
  }

  /** 确定性模拟步进（无头验证/回归基线用）：以固定 1/60 步进接管内部时钟，rAF 退化为纯渲染。
   * Phase 5 十三姿态安全验证与动画回归都以此为准，摆脱 rAF/虚拟时钟的不确定性。 */
  advanceSim(seconds: number): void {
    if (!this.simPaused) {
      this.simPaused = true
      this.simNow = this.lastNow
    }

    let remaining = Math.max(0, seconds)

    while (remaining > 1e-6) {
      const dt = Math.min(1 / 60, remaining)
      this.simNow += dt * 1000
      this.tickBody(this.simNow, { dt, render: false })
      remaining -= dt
    }
  }

  /** tickBody 内部复用的目标参数草稿（避免每帧 { ...this.target } 分配 ~38 字段的对象）。
   *  不能直接暴露给外部写入；调用方仍通过 this.target 修改，tickBody 复制字段。 */
  private readonly tgtScratch: PuppetParams = defaultPuppetParams()
  /** Evaluated 复用的草稿（避免每帧 spread 38 字段的对象）。 */
  private readonly evalScratch: Evaluated = defaultPuppetParams() as unknown as Evaluated
  /** updateSkeleton 内部复用的腿部 IK 目标坐标缓冲（避免每帧步态计算分配对象）。 */
  private readonly legTargetScratch = { x: 0, y: 0 }

  private tickBody(now: number, opts: { dt?: number; render?: boolean } = {}): void {
    const A = this.anchors

    if (!this.layers.length || !A) {
      return
    }

    const dt = opts.dt ?? Math.min(0.05, (now - this.lastNow) / 1000)
    this.lastNow = now
    const t = now / 1000
    // 复用 tgtScratch，字段级复制（避免每帧分配 38 字段的 PuppetParams）
    const tgt = this.tgtScratch
    const src = this.target
    const tgtAny = tgt as unknown as Record<string, unknown>
    const srcAny = src as unknown as Record<string, unknown>

    for (let i = 0; i < PARAM_KEYS.length; i++) {
      const k = PARAM_KEYS[i]!
      tgtAny[k] = srcAny[k]
    }

    if (this.auto.idle) {
      const is = this.idleScale
      tgt.angleX += (0.13 * Math.sin(t * 0.42) + 0.05 * Math.sin(t * 1.13)) * is
      tgt.angleY += 0.08 * Math.sin(t * 0.31 + 1.7) * is
      tgt.angleZ += 0.07 * Math.sin(t * 0.23 + 0.5) * is
      tgt.body += 0.1 * Math.sin(t * 0.19 + 2.1) * is
    }

    // 视线优先于漫游：有焦点时眼先行（全幅、高速率），头与身体小幅滞后跟随
    const gz = this.auto.gaze && this.gaze && now < this.gazeUntil ? this.gaze : null

    if (gz) {
      tgt.eyeX = clamp(tgt.eyeX * 0.3 + gz.x, -1, 1)
      tgt.eyeY = clamp(tgt.eyeY * 0.3 + gz.y * 0.85, -1, 1)
      tgt.angleX = clamp(tgt.angleX + gz.x * 0.42, -1, 1)
      tgt.angleY = clamp(tgt.angleY - gz.y * 0.26, -1, 1)
      tgt.angleZ = clamp(tgt.angleZ + gz.x * 0.06, -1, 1)
      tgt.body = clamp(tgt.body + gz.x * 0.1, -1, 1)
    } else if (this.auto.rand) {
      // 种子化自主观察段落（Phase 4）：~15s 环内依次左右观察、抬头、低头，动作间回正；
      // 同种子同时间序列 → 相同动作（无每帧随机），眼先于头指向目标
      if (this.segStep < 0 || now >= this.segAt + this.segDur) {
        this.advanceSeg(now)
      }

      const p = smooth(clamp((now - this.segAt) / this.segDur, 0, 1))
      const ax = this.segFrom.ax + (this.segTo.ax - this.segFrom.ax) * p
      const ay = this.segFrom.ay + (this.segTo.ay - this.segFrom.ay) * p
      tgt.angleX = clamp(tgt.angleX + ax, -1, 1)
      tgt.angleY = clamp(tgt.angleY + ay, -1, 1)
      tgt.eyeX = clamp(tgt.eyeX + ax * 0.8, -1, 1)
      tgt.eyeY = clamp(tgt.eyeY - ay * 0.4, -1, 1)
    }

    // 微扫视：注视/漫游之上叠加小幅快速眼动，指数衰减，避免目光发死
    if (this.auto.rand) {
      if (now > this.nextSac) {
        this.nextSac = now + 250 + Math.random() * 1100
        this.sac.x = (Math.random() * 2 - 1) * 0.09
        this.sac.y = (Math.random() * 2 - 1) * 0.05
      }

      const decay = Math.exp(-dt * 1.8)
      this.sac.x *= decay
      this.sac.y *= decay
      tgt.eyeX = clamp(tgt.eyeX + this.sac.x, -1, 1)
      tgt.eyeY = clamp(tgt.eyeY + this.sac.y, -1, 1)
    }

    if (this.auto.talk) {
      if (now > this.nextTalkState) {
        this.talkOn = !this.talkOn
        this.nextTalkState = now + (this.talkOn ? 1200 + Math.random() * 2200 : 600 + Math.random() * 1800)

        if (this.talkOn) {
          this.talkAmp = 0.55 + Math.random() * 0.45
        }
      }

      if (this.talkOn && now > this.nextSyl) {
        this.nextSyl = now + 70 + Math.random() * 110
        this.talkTgt = (Math.random() < 0.25 ? 0.04 : 0.25 + Math.random() * 0.75) * this.talkAmp
        this.talkFTgt = (Math.random() * 2 - 1) * 0.6
      }

      if (!this.talkOn) {
        this.talkTgt = 0
        this.talkFTgt = 0
      }

      this.talkV += (this.talkTgt - this.talkV) * Math.min(1, dt * 22)
      this.talkF += (this.talkFTgt - this.talkF) * Math.min(1, dt * 10)
      tgt.mouthOpen = Math.max(tgt.mouthOpen, this.talkV)
      tgt.mouthForm = clamp(tgt.mouthForm + this.talkF, -1, 1)
    }

    if (this.auto.blink) {
      if (this.blinkT < 0 && now > this.nextBlink) {
        this.blinkT = 0
        this.blinkFloor = Math.random() < 0.2 ? 0.25 + Math.random() * 0.25 : 0
        this.nextBlink = now + 2000 + Math.random() * 5000

        if (Math.random() < 0.16) {
          this.nextBlink = now + 260
        }
      }

      if (this.blinkT >= 0) {
        this.blinkT += dt
        const d = this.blinkT
        const fl = this.blinkFloor
        let v: number

        if (d < 0.08) {
          v = 1 - (1 - fl) * (d / 0.08)
        } else if (d < 0.22) {
          v = fl
        } else if (d < 0.34) {
          v = fl + (1 - fl) * ((d - 0.22) / 0.12)
        } else {
          v = 1
          this.blinkT = -1
        }

        tgt.eyeOpenL = Math.min(tgt.eyeOpenL, v)
        tgt.eyeOpenR = Math.min(tgt.eyeOpenR, v)
      }
    }

    // 上身同源跟随（Phase 3）：直接读平滑后的头部偏航、小比例同刻转向 —
    // 不经过第二套慢响应器，头/颈/肩不会因时间差断开
    tgt.body = clamp(tgt.body + this.cur.angleX * 0.24, -1, 1)

    const curAny = this.cur as unknown as Record<string, unknown>

    for (let i = 0; i < PARAM_KEYS.length; i++) {
      const key = PARAM_KEYS[i]!

      if (i === IK_HAND_L || i === IK_HAND_R) {
        const tk = tgt[key] as { x: number; y: number } | null
        const ck = this.cur[key] as { x: number; y: number } | null

        if (!tk) {
          if (ck) {
            curAny[key] = null
          }
        } else if (!ck) {
          curAny[key] = { x: tk.x, y: tk.y }
        } else {
          const rate = Math.min(1, dt * 16)
          ck.x += (tk.x - ck.x) * rate
          ck.y += (tk.y - ck.y) * rate
        }
      } else if (i === IK_FOOT_L || i === IK_FOOT_R) {
        const tk = tgt[key] as { dx: number; dy: number } | null
        const ck = this.cur[key] as { dx: number; dy: number } | null

        if (!tk) {
          if (ck) {
            curAny[key] = null
          }
        } else if (!ck) {
          curAny[key] = { dx: tk.dx, dy: tk.dy }
        } else {
          const rate = Math.min(1, dt * 16)
          ck.dx += (tk.dx - ck.dx) * rate
          ck.dy += (tk.dy - ck.dy) * rate
        }
      } else {
        const tVal = tgt[key] as number
        const cVal = this.cur[key] as number
        curAny[key] = cVal + (tVal - cVal) * Math.min(1, dt * (PARAM_RATE[key] ?? 14))
      }
    }

    const e = this.evalScratch
    const eAny = e as unknown as Record<string, unknown>

    for (let i = 0; i < PARAM_KEYS.length; i++) {
      const k = PARAM_KEYS[i]!
      eAny[k] = curAny[k]
    }

    e.breath = 0
    e.breathHead = 0
    e.simT = t
    e.earLift = 0
    e.ahogeDy = 0

    // 非对称呼吸（3.4s 周期，吸气快呼气慢）+ 每 18~38s 一次深呼吸；头部相位略滞后。
    // frozen（Phase 5）冻结相位与调度，姿态定格逐位可复现
    if (this.frozen) {
      this.breathP = 0
      this.sighUntil = 0
    } else {
      this.breathP += dt / 3.4

      if (now > this.nextSigh) {
        this.nextSigh = now + 18000 + Math.random() * 20000
        this.sighUntil = now + 3400
      }
    }

    const bAmp = this.frozen ? 0 : now < this.sighUntil ? 1.5 : 1
    const bp = this.breathP % 1
    e.breath = this.frozen ? 0 : clamp(breathCurve(bp) * bAmp, 0, 1.5)
    e.breathHead = breathCurve((bp + 0.94) % 1) * bAmp

    // 耳事件（Phase 4，种子化调度）：偶发连续快速抬落约 4 次，随后严格回中立；
    // 与自主段落同门控（auto.rand），姿态定格/验证时保持确定性
    if (this.auto.rand) {
      if (this.earEvT0 < 0 && now > this.earNext) {
        this.earEvT0 = now
        this.earNext = now + 14000 + this.rng() * 16000
      }
    } else {
      this.earEvT0 = -1
    }

    if (this.earEvT0 >= 0) {
      const d = (now - this.earEvT0) / 1000

      if (d > 1.3) {
        this.earEvT0 = -1
      } else {
        e.earLift = Math.abs(Math.sin(d * 9)) * (1 - d / 1.3) * 4.5 * this.fs
      }
    }

    // 呆毛（Phase 4）：平时随发横摆（发束链自动），明显纵向弹动由种子化偶发事件激发；
    // 与耳事件同受 auto.rand 门控，保证姿态定格确定性
    if (this.auto.rand) {
      if (this.ahogeEvT0 < 0 && now > this.ahogeNext) {
        this.ahogeEvT0 = now
        this.ahogeNext = now + 9000 + this.rng() * 12000
        this.ahogeS.v += (this.rng() < 0.5 ? -1 : 1) * 60
      }

      if (this.ahogeEvT0 >= 0 && (now - this.ahogeEvT0) / 1000 > 1.6) {
        this.ahogeEvT0 = -1
      }
    }

    {
      const at = -this.cur.angleY * 10 * this.fs
      const kk = 120
      const cc = 3.5
      const aa = -kk * (this.ahogeS.x - at) - cc * this.ahogeS.v
      this.ahogeS.v += aa * dt
      this.ahogeS.x += this.ahogeS.v * dt
      e.ahogeDy = this.ahogeS.x
    }

    this.lastE = e

    // 发束链驱动（Phase 4）：头/身位移 60/40 混合 + 风动；根节点硬跟随、
    // 下游节点逐节追踪父节点（自由端逐步获得惯性），刚度/阻尼沿链递减
    const headDX = (e.angleX * 14 + e.angleZ * 0.07 * (A.neckPivot.cy - A.face.cy)) * this.fs
    const bodyDX = e.body * 8 * this.fs

    for (const L of this.layers) {
      if (!L.spr) {
        continue
      }

      const isFH = L.bn === 'front hair'
      const softK = 1 + 0.5 * ((isFH ? e.fhSoft : e.soft) / 3)

      for (const sp of L.spr) {
        const wind = this.auto.idle ? 1.8 * Math.sin(t * 0.8 + sp.phase) + 1.0 * Math.sin(t * 1.9 + sp.phase * 2.3) : 0
        const txv = headDX + bodyDX * 0.66 + wind * this.fs
        let prev = txv

        for (let i = 0; i < sp.nodes.length; i++) {
          const nd = sp.nodes[i]!
          const kk = 80 - i * 14
          const cc = (8 - i * 1.6) * softK
          const axv = -kk * (nd.x - prev) - cc * nd.v
          nd.v += axv * dt
          nd.x += nd.v * dt
          prev = nd.x
        }
      }
    }

    {
      const bustTgt = (e.breath * 3.0 - e.angleY * 6.0 + e.body * 4.0) * this.fs
      const kk = 140
      const cc = 4.2
      const aa = -kk * (this.bounce.x - bustTgt) - cc * this.bounce.v
      this.bounce.v += aa * dt
      this.bounce.x += this.bounce.v * dt
      this.bounce.dy = -(this.bounce.x - bustTgt) * 3.0
    }

    if (opts.render !== false) {
      this.render()
    }
  }

  private render(): void {
    const gl = this.gl
    const e = this.lastE

    if (!e || !this.layers.length) {
      return
    }

    if (this.skeleton) {
      this.updateSkeleton(e)
    }

    gl.viewport(0, 0, this.cw, this.ch)
    gl.clearColor(0, 0, 0, 0)
    gl.clearStencil(0)
    gl.clear(gl.COLOR_BUFFER_BIT | gl.STENCIL_BUFFER_BIT)
    gl.uniform2f(this.locRes, this.cw, this.ch)

    for (const L of this.layers) {
      const fa = this.fadeAlpha(L, e)

      if (fa < 0.004 && !(L.fade === 'eyeOpen' && L.isEyewhite)) {
        continue
      }

      this.deform(L, e)
      gl.uniform1f(this.locAlpha, fa)
      gl.bindBuffer(gl.ARRAY_BUFFER, L.vboPos)
      gl.bufferData(gl.ARRAY_BUFFER, L.cur, gl.DYNAMIC_DRAW)
      gl.vertexAttribPointer(this.locPos, 2, gl.FLOAT, false, 0, 0)
      gl.bindBuffer(gl.ARRAY_BUFFER, L.vboUV)
      gl.vertexAttribPointer(this.locUV, 2, gl.FLOAT, false, 0, 0)
      gl.bindTexture(gl.TEXTURE_2D, L.tex)
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, L.ibo)

      if (L.isEyewhite) {
        gl.enable(gl.STENCIL_TEST)
        gl.stencilFunc(gl.ALWAYS, 1, 0xff)
        gl.stencilOp(gl.KEEP, gl.KEEP, gl.REPLACE)
        gl.uniform1f(this.locCut, 0.25)
        gl.drawElements(gl.TRIANGLES, L.nIdx, gl.UNSIGNED_SHORT, 0)
        gl.disable(gl.STENCIL_TEST)
        gl.uniform1f(this.locCut, 0.0)
      } else if (L.isIrides) {
        gl.enable(gl.STENCIL_TEST)
        gl.stencilFunc(gl.EQUAL, 1, 0xff)
        gl.stencilOp(gl.KEEP, gl.KEEP, gl.KEEP)
        gl.drawElements(gl.TRIANGLES, L.nIdx, gl.UNSIGNED_SHORT, 0)
        gl.disable(gl.STENCIL_TEST)
      } else {
        gl.drawElements(gl.TRIANGLES, L.nIdx, gl.UNSIGNED_SHORT, 0)
      }
    }
  }
}

/** PSD 字节 → rig 并应用到 runtime；vendor 前置检查与差分合成选项在此收敛。 */
export async function loadPsdIntoRuntime(runtime: PuppetRuntime, psdBuffer: ArrayBuffer): Promise<Rig> {
  await ensureVendorLibs()
  const Rigger = window.Rigger
  const agPsd = window.agPsd

  if (!Rigger || !agPsd) {
    throw new Error('puppet vendor libs not loaded (rigger.js / ag-psd.min.js)')
  }

  const psd = agPsd.readPsd(new Uint8Array(psdBuffer), { useImageData: true, skipThumbnail: true })
  Rigger.cleanPsdLayers(psd)
  const GP = window.GenericParts
  const generic: Record<string, RigImage> = {}

  if (GP) {
    for (const key of ['eyeL', 'eyeR', 'mouth'] as const) {
      const img = GP.get(key)

      if (img) {
        generic[key] = img
      }
    }
  }

  const opts = Object.keys(generic).length ? { generic } : {}
  const rig = Rigger.buildRig(psd, opts)

  if (rig.warnings.length) {
    log.warn('puppet-runtime', 'rig warnings', rig.warnings)
  }

  runtime.applyRig(rig)

  return rig
}
