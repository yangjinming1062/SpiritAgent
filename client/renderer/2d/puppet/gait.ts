/** 2D 复合步态驱动器（GaitDriver）与贴边趴姿（Edge Cling Pose）常量表。
 *
 * 核心设计：
 * 1. 步态（Locomotion）：基于逐帧位移积分相位（phase += (dist / STRIDE_PX) * 2pi），
 *    非墙钟推进，实现 easeInOut 减速段步频自然放缓与零脚滑；叠加 EMA 速度比率与
 *    指数平滑包络（tau_in ~150ms / tau_out ~350ms）。
 * 2. 贴边趴姿（Edge Cling Pose）：store 门控的持续型 overlay，左右镜像反转反对称组符号
 *    （body/angleX/angleZ 反转；angleY 俯仰与 armY/physAmp/fhAmp/eyeCY 保持对称）。
 * 3. 呼吸衰减（Respiration Attenuation）：走路时 0.7，趴姿时 0.4。
 */

import { clamp } from '@runtime'

import type { Locomotion } from '@/companion'

const STRIDE_PX = 36
const WALK_SPEED = 80

interface GaitPose {
  angleX: number
  angleY: number
  angleZ: number
  body: number
  armY: number
  armPos: number
  eyeCY: number
  physAmp: number
  fhAmp: number
}

/** 调这里：贴边趴姿基准（右贴边）。「站在屏外、上半身探入」的大倾角由舞台层的
 * EDGE_DOCK_LEAN_DEG 整体旋转完成，这里只负责贴身次级姿态：转头看向屏内、手扒边缘、
 * 微低头俯视。通道语义——angleX 水平转身(正=朝观众右转)/angleY 俯仰(正=抬头)/
 * angleZ 歪头/body 水平横移/armY 竖直抬手/armPos 竖直下压；左贴边由 mirrorClingPose 翻反对称组 */
const EDGE_CLING_BASE_POSE: GaitPose = {
  armY: 1.8, // 手扒在屏幕边缘上（抬手 ~46px）
  armPos: 0,
  body: -0.15,
  angleX: -0.4, // 头转向屏内张望
  angleY: -0.06, // 微低头，趴边俯视屏内
  angleZ: -0.08,
  eyeCY: 0.25,
  physAmp: 0.2,
  fhAmp: 0.2
}

/** 镜像趴姿：根据左右贴边侧翻转反对称通道符号，并根据装配档位进行幅度缩放 */
function mirrorClingPose(side: 'left' | 'right', tier: 'semantic' | 'grouped' | 'minimal' = 'grouped'): GaitPose {
  const scale = tier === 'semantic' ? 1.0 : 0.85
  const isLeft = side === 'left'

  return {
    armY: EDGE_CLING_BASE_POSE.armY * scale,
    physAmp: EDGE_CLING_BASE_POSE.physAmp * scale,
    fhAmp: EDGE_CLING_BASE_POSE.fhAmp * scale,
    eyeCY: EDGE_CLING_BASE_POSE.eyeCY * scale,
    armPos: EDGE_CLING_BASE_POSE.armPos * scale,
    angleY: EDGE_CLING_BASE_POSE.angleY * scale,
    // 反对称：左贴边身体向右倾/头向右转，右贴边身体向左倾/头向左转
    body: (isLeft ? -EDGE_CLING_BASE_POSE.body : EDGE_CLING_BASE_POSE.body) * scale,
    angleX: (isLeft ? -EDGE_CLING_BASE_POSE.angleX : EDGE_CLING_BASE_POSE.angleX) * scale,
    angleZ: (isLeft ? -EDGE_CLING_BASE_POSE.angleZ : EDGE_CLING_BASE_POSE.angleZ) * scale
  }
}

export interface GaitFrameOutput {
  /** 步态增量通道（可加性叠加） */
  gaitOffsets: {
    body: number
    angleZ: number
    angleX: number
    fhAmp: number
    physAmp: number
  }
  /** 贴边趴姿目标（若未贴边则为 null） */
  clingPose: GaitPose | null
  /** 贴边姿态过渡权重 [0, 1] */
  clingWeight: number
  /** 步态包络强度 [0, 1] */
  gaitEnvelope: number
  /** 待机呼吸衰减系数 [0.4, 1.0] */
  idleScale: number
  /** 迈步双脚局部偏移量 (dx, dy) 相对绑定姿态踝部；未迈步或完全静止为 null */
  footIK: {
    l: { dx: number; dy: number } | null
    r: { dx: number; dy: number } | null
  }
  /** 对侧摆臂角度（弧度制） */
  armSwing: {
    l: number
    r: number
  }
  /** 是否处于有效迈步状态 */
  isWalking: boolean
  /** 贴边侧 */
  clingDockSide: 'none' | 'left' | 'right'
}

export class GaitDriver {
  private phase = 0
  private smoothV = 0
  private gA = 0
  private w = 0
  private lastDx = 0
  /** 行走方向符号锁存：阈值 + 迟滞，避免一次抖动让 dx 反向导致全身 IK 单帧反向。 */
  private lastWalkDir: 0 | -1 | 1 = 0
  // 持久化复用缓冲：每帧一次 update 不再分配 7+ 个临时对象
  private readonly outGaitOffsets = { body: 0, angleZ: 0, angleX: 0, fhAmp: 0, physAmp: 0 }
  private readonly outFootL = { dx: 0, dy: 0 }
  private readonly outFootR = { dx: 0, dy: 0 }
  private readonly outFootIK = {
    l: null as { dx: number; dy: number } | null,
    r: null as { dx: number; dy: number } | null
  }
  private readonly outArmSwing = { l: 0, r: 0 }
  private readonly outClingPose: GaitPose = {
    angleX: 0,
    angleY: 0,
    angleZ: 0,
    body: 0,
    armY: 0,
    armPos: 0,
    eyeCY: 0,
    physAmp: 0,
    fhAmp: 0
  }
  private outClingPoseSide: 'none' | 'left' | 'right' = 'none'
  private outClingPoseTier: 'semantic' | 'grouped' | 'minimal' = 'grouped'

  /** 供启动恢复或外部测试直接置位贴边权重 */
  setClingWeight(weight: number): void {
    this.w = clamp(weight, 0, 1)
  }

  getClingWeight(): number {
    return this.w
  }

  getGaitEnvelope(): number {
    return this.gA
  }

  getPhase(): number {
    return this.phase
  }

  /** 每帧推进步态状态并计算输出增量与姿态目标。
   *  返回的 GaitFrameOutput 复用同一组缓冲对象；调用方应在同一帧消费完字段后再调用下一次。 */
  update(
    dt: number,
    dx: number,
    dy: number,
    locomotion: Locomotion,
    isEdgeDocked: boolean,
    edgeDockSide: 'none' | 'left' | 'right',
    tier: 'semantic' | 'grouped' | 'minimal' = 'grouped'
  ): GaitFrameOutput {
    const clampedDt = clamp(dt, 0.001, 0.1)
    const dist = Math.hypot(dx, dy)
    const isWalk = !isEdgeDocked && (locomotion === 'walk' || locomotion === 'walk_fast')

    // 1. 速度比率与 EMA 平滑
    if (isWalk && clampedDt > 0) {
      const instantV = dist > 0 ? dist / clampedDt : this.smoothV || WALK_SPEED
      this.smoothV += (instantV - this.smoothV) * Math.min(1, clampedDt * 10)
    } else {
      this.smoothV += (0 - this.smoothV) * Math.min(1, clampedDt * 8)
    }

    const s = clamp(this.smoothV / WALK_SPEED, 0, 1)

    // 2. 相位积分（按位移距离积分，非纯墙钟时间）
    if (isWalk) {
      this.phase += (dist / STRIDE_PX) * 2 * Math.PI

      // 兜底：若位移过小但明确在 walk，维持最低相位推进速率防卡住
      if (dist < 0.02 * clampedDt * WALK_SPEED) {
        this.phase += clampedDt * (WALK_SPEED / STRIDE_PX) * 0.4 * 2 * Math.PI
      }

      this.phase %= 2 * Math.PI

      if (dx !== 0) {
        this.lastDx = dx
      }
    }

    // 3. 步态包络淡入淡出（tau_in ~150ms, tau_out ~350ms）
    if (isWalk) {
      this.gA += (1 - this.gA) * (1 - Math.exp(-clampedDt / 0.15))
    } else {
      this.gA += (0 - this.gA) * (1 - Math.exp(-clampedDt / 0.35))

      if (this.gA < 0.001) {
        this.gA = 0
      }
    }

    // 4. 贴边趴姿权重渐变（tau ~200ms）
    const targetW =
      isEdgeDocked && edgeDockSide !== 'none' && locomotion !== 'drag' && locomotion !== 'fly' && locomotion !== 'jump'
        ? 1
        : 0

    this.w += (targetW - this.w) * (1 - Math.exp(-clampedDt / 0.2))

    if (Math.abs(targetW - this.w) < 0.001) {
      this.w = targetW
    }

    // 5. 步态通道增量计算
    const tierScale = tier === 'semantic' ? 1.0 : 0.6
    const isFast = locomotion === 'walk_fast'
    const phaseMult = isFast ? 1.35 : 1.0
    const ampMult = (isFast ? 1.5 : 1.0) * this.gA * tierScale
    const effPhase = this.phase * phaseMult

    // bob：躯干上下起伏（一步两颠）
    const bob = 0.1 * Math.sqrt(Math.max(0.2, s)) * Math.sin(effPhase * 2) * ampMult
    // sway：身体左右摆动（侧倾）
    const sway = 0.08 * Math.sin(effPhase - Math.PI / 2) * s * ampMult
    // facing：位移方向的微倾线索
    const facing = 0.15 * Math.sign(this.lastDx || 1) * s * ampMult
    // 次级物理：前发弹簧低通激励与全身物理增强
    const fhAmpBonus = Math.pow(Math.sin(effPhase), 2) * 0.35 * s * ampMult
    const physAmpBonus = 0.25 * s * ampMult

    const gaitOffsets = this.outGaitOffsets
    gaitOffsets.body = bob
    gaitOffsets.angleZ = sway
    gaitOffsets.angleX = facing * 0.55 + 0.02 * bob
    gaitOffsets.fhAmp = fhAmpBonus
    gaitOffsets.physAmp = physAmpBonus

    // 6. 迈步双脚 IK 偏移计算（支撑相沿行走反向补偿 dx，摆动相向前抬腿划弧）
    const footIK = this.outFootIK
    let footL: { dx: number; dy: number } | null = null
    let footR: { dx: number; dy: number } | null = null
    let armSwingL = 0
    let armSwingR = 0
    const isWalking = isWalk && this.gA > 0.01

    if (this.gA > 0.005) {
      const stride = STRIDE_PX * (isFast ? 1.15 : 1.0) * s
      const liftMax = 18 * (isFast ? 1.25 : 1.0) * s * ampMult
      const walkDir = Math.sign(this.lastDx || 1)

      // 左腿相位 [0, 2pi)
      const phiL = ((effPhase % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI)
      // 右腿相位与左腿差 pi
      const phiR = (phiL + Math.PI) % (2 * Math.PI)

      const calcFootOffset = (phi: number, out: { dx: number; dy: number }): void => {
        if (phi < Math.PI) {
          // 支撑相 (0 -> pi)：脚掌在地面反向平移，抵消窗口位移
          const u = phi / Math.PI // 0 -> 1
          out.dx = (0.5 - u) * stride * walkDir * ampMult
          out.dy = 0
        } else {
          // 摆动相 (pi -> 2pi)：脚掌离地抬起，向前划弧落地
          const u = (phi - Math.PI) / Math.PI // 0 -> 1
          out.dx = -Math.cos(u * Math.PI) * 0.5 * stride * walkDir * ampMult
          out.dy = -Math.sin(u * Math.PI) * liftMax
        }
      }

      calcFootOffset(phiL, this.outFootL)
      calcFootOffset(phiR, this.outFootR)
      // 停步期间 gA 趋零：用 gA 本身作为 IK 幅度斜坡（线性 0..0.05），
      // 而不是检查双脚 dx/dy < 0.2（phiL 与 phiR 始终 π 偏离，几何上不可能同时小）。
      // gA < 0.005 时彻底归零，让 updateSkeleton 把腿复位到 bind 姿态。
      const ramp = Math.min(1, this.gA / 0.05)
      footL = this.outFootL
      footR = this.outFootR
      this.outFootL.dx *= ramp
      this.outFootL.dy *= ramp
      this.outFootR.dx *= ramp
      this.outFootR.dy *= ramp

      if (this.gA < 0.005) {
        footL = null
        footR = null
      }

      // 对侧摆臂：左臂与右腿同相，右臂与左腿同相。
      // walkDir 用符号锁存 + 死区阈值（防止一次 jitter 立刻反向），避免来回踱步时
      // dx 符号瞬变让脚/臂 IK 单帧反向穿模。
      if (Math.abs(this.lastDx) > 2 || this.lastWalkDir === 0) {
        const sign = Math.sign(this.lastDx) as -1 | 0 | 1
        this.lastWalkDir = sign !== 0 ? sign : this.lastWalkDir !== 0 ? this.lastWalkDir : 1
      }

      const safeWalkDir = this.lastWalkDir
      armSwingL = Math.sin(phiR) * 0.32 * s * ampMult * safeWalkDir
      armSwingR = Math.sin(phiL) * 0.32 * s * ampMult * safeWalkDir
    }

    footIK.l = footL
    footIK.r = footR
    const armSwing = this.outArmSwing
    armSwing.l = armSwingL
    armSwing.r = armSwingR

    // 7. 贴边姿态：edgeDockSide 切到 'none' 但 w > 0 时继续返回最后一次镜像，
    //    让 PuppetStage 的 lerp 系数 w 自行完成 200ms 衰减，避免 body/eyeCY/handIK 单帧跳回。
    let clingPose: GaitPose | null = null
    let effectiveDockSide: 'none' | 'left' | 'right' = 'none'

    if (edgeDockSide !== 'none') {
      effectiveDockSide = edgeDockSide

      if (this.outClingPoseSide !== edgeDockSide || this.outClingPoseTier !== tier) {
        const mirrored = mirrorClingPose(edgeDockSide, tier)
        const cachedAny = this.outClingPose as unknown as Record<string, number>
        const mirroredAny = mirrored as unknown as Record<string, number>

        for (const k of CLING_POSE_KEYS) {
          cachedAny[k] = mirroredAny[k]!
        }

        this.outClingPoseSide = edgeDockSide
        this.outClingPoseTier = tier
      }

      clingPose = this.outClingPose
    } else if (this.w > 0.001 && this.outClingPoseSide !== 'none') {
      // 衰减中：保持上一次的镜像姿态与 side，让调用方按 w 衰减
      effectiveDockSide = this.outClingPoseSide
      clingPose = this.outClingPose
    }

    // 8. 呼吸衰减系数：走路衰减至 0.7，趴姿衰减至 0.4
    const idleScale = Math.max(0.2, (1 - 0.6 * this.w) * (1 - 0.3 * this.gA))

    return {
      gaitOffsets,
      clingPose,
      clingWeight: this.w,
      gaitEnvelope: this.gA,
      idleScale,
      footIK,
      armSwing,
      isWalking,
      clingDockSide: effectiveDockSide
    }
  }
}

const CLING_POSE_KEYS: readonly (keyof GaitPose)[] = Object.freeze([
  'angleX',
  'angleY',
  'angleZ',
  'body',
  'armY',
  'armPos',
  'eyeCY',
  'physAmp',
  'fhAmp'
] as (keyof GaitPose)[])
