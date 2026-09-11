import type { PuppetRuntime } from './puppet-runtime'

// 动作 → 定时包络（通用语义子集；未注册键忽略）

export interface ActionEnvelope {
  durMs: number
  /** 触发首帧一次性执行（冲量等） */
  onStart?: (rt: PuppetRuntime) => void
  /** k ∈ [0,1]；结束帧 k=1 后 touched 通道写回 ACTION_DEFAULTS */
  apply: (k: number, rt: PuppetRuntime) => void
}

/** 动作结束帧立即写回默认值的「冲量式」通道（眼动、倾角、身体横移、眨眼）。
 *  写回后由下一帧的平滑器接管，与持续姿态无冲突。 */
export const ACTION_IMPULSE_DEFAULTS: Partial<Record<keyof PuppetRuntime['target'], number>> = {
  angleX: 0,
  angleY: 0,
  angleZ: 0,
  body: 0,
  eyeX: 0,
  eyeCY: 0,
  eyeOpenL: 1,
  eyeOpenR: 1
}

/** 平滑过渡式 IK 通道（手/脚目标、对侧摆臂）。这些通道在贴边/走路时由
 *  GaitDriver 持续写入，动作结束帧不能粗暴 null/0，否则会一帧把贴边手或
 *  摆动中的脚拉回 bind pose。只有在 gaitEnvelope/clingWeight 都已基本衰减
 *  时才写回默认值，让持续姿态接管。 */
export const ACTION_SMOOTHED_DEFAULTS: Partial<
  Record<keyof PuppetRuntime['target'], number | { x: number; y: number } | { dx: number; dy: number } | null>
> = {
  handIKL: null,
  handIKR: null,
  footIKL: null,
  footIKR: null,
  armSwingL: 0,
  armSwingR: 0
}

function getArmGeometry(
  rt: PuppetRuntime,
  side: 'L' | 'R'
): { sh: { x: number; y: number }; hand: { x: number; y: number }; len: number } | null {
  const skel = rt.getSkeleton()

  if (!skel) {
    return null
  }

  const sh = skel.getBone(side === 'L' ? 'shoulderL' : 'shoulderR')?.bindWorldPos
  const hand = skel.getBone(side === 'L' ? 'handL' : 'handR')?.bindWorldPos

  if (!sh || !hand) {
    return null
  }

  const len = Math.hypot(hand.x - sh.x, hand.y - sh.y)

  return { sh, hand, len }
}

/** 把手腕 IK 目标按 s ∈ [0,1] 从当前绑定位插值到 (targetX, targetY)；写入 rt.target.handIK{L|R}。 */
function lerpHandIK(rt: PuppetRuntime, side: 'L' | 'R', s: number, targetX: number, targetY: number): void {
  const geom = getArmGeometry(rt, side)

  if (!geom) {
    return
  }

  const key = side === 'L' ? 'handIKL' : 'handIKR'
  const x = geom.hand.x * (1 - s) + targetX * s
  const y = geom.hand.y * (1 - s) + targetY * s
  const cur = rt.target[key]

  if (cur) {
    cur.x = x
    cur.y = y
  } else {
    rt.target[key] = { x, y }
  }
}

/** 工厂：把镜像的左右动作压缩为一份表，避免 wave_left/right 等 9 对复制。
 *  sign = +1 表示右（phi/目标 X/eyeX 取正），-1 表示左。 */
interface ArmActionParams {
  durMs: number
  profile: (
    k: number,
    s: number,
    rt: PuppetRuntime,
    side: 'L' | 'R',
    sign: number
  ) => { targetX: number; targetY: number; s: number } | null
  /** 复用相位与 amp：getter 返回 (s, head/body 等其它参数) */
  extras: (k: number, sign: number, s: number, rt: PuppetRuntime) => void
}

function makeArmAction(params: ArmActionParams, side: 'L' | 'R'): ActionEnvelope {
  const sign = side === 'L' ? -1 : 1

  return {
    durMs: params.durMs,
    apply: (k, rt) => {
      const phase = params.profile(k, 0, rt, side, sign)

      if (phase) {
        lerpHandIK(rt, side, phase.s, phase.targetX, phase.targetY)
      }

      params.extras(k, sign, phase?.s ?? 0, rt)
    }
  }
}

const WAVE_PROFILE = (k: number, _s: number, rt: PuppetRuntime, side: 'L' | 'R', sign: number) => {
  const geom = getArmGeometry(rt, side)

  if (!geom) {
    return null
  }

  const s = Math.sin(Math.min(1, Math.max(0, k)) * Math.PI)
  const phi = 0.35 + Math.sin(k * Math.PI * 3) * 0.38 * sign
  const r = geom.len * 0.82

  return { targetX: geom.sh.x + Math.cos(phi) * r * sign, targetY: geom.sh.y - Math.sin(phi) * r, s }
}

const PRESENT_PROFILE = (k: number, _s: number, rt: PuppetRuntime, side: 'L' | 'R', sign: number) => {
  const geom = getArmGeometry(rt, side)

  if (!geom) {
    return null
  }

  const s = Math.sin(Math.min(1, k * 1.4) * Math.PI)
  const r = geom.len * 0.78

  return { targetX: geom.sh.x + r * 0.8 * sign, targetY: geom.sh.y + r * 0.55, s }
}

const POINT_PROFILE = (k: number, _s: number, rt: PuppetRuntime, side: 'L' | 'R', sign: number) => {
  const geom = getArmGeometry(rt, side)

  if (!geom) {
    return null
  }

  const s = Math.sin(Math.min(1, k * 1.5) * Math.PI)
  const r = geom.len * 0.88

  return { targetX: geom.sh.x + r * 0.92 * sign, targetY: geom.sh.y + r * 0.15, s }
}

const WAVE_EXTRAS = (k: number, sign: number, _s: number, rt: PuppetRuntime): void => {
  rt.target.angleZ = Math.sin(k * Math.PI) * 0.1 * sign
}

const PRESENT_EXTRAS = (k: number, sign: number, s: number, rt: PuppetRuntime): void => {
  rt.target.eyeX = 0.4 * Math.sin(k * Math.PI) * sign
  rt.target.armPos = s * 0.7
}

const POINT_EXTRAS = (k: number, sign: number, s: number, rt: PuppetRuntime): void => {
  rt.target.eyeX = 0.7 * Math.sin(k * Math.PI) * sign
  rt.target.angleX = 0.3 * Math.sin(k * Math.PI) * sign
  rt.target.armY = -s * 0.6
}

// 键集与后端 manifest_exporter DEFAULT_ACTIONS 白名单对齐（docs/PROTOCOL.md §3）；
// 未列出的白名单键按忽略处理（与 mesh2d 忽略未注册 action 同策略）。
export const ACTIONS: Record<string, ActionEnvelope> = {
  wave_right: makeArmAction({ durMs: 1300, profile: WAVE_PROFILE, extras: WAVE_EXTRAS }, 'R'),
  wave_left: makeArmAction({ durMs: 1300, profile: WAVE_PROFILE, extras: WAVE_EXTRAS }, 'L'),
  present_right: makeArmAction({ durMs: 1600, profile: PRESENT_PROFILE, extras: PRESENT_EXTRAS }, 'R'),
  present_left: makeArmAction({ durMs: 1600, profile: PRESENT_PROFILE, extras: PRESENT_EXTRAS }, 'L'),
  point_right: makeArmAction({ durMs: 1400, profile: POINT_PROFILE, extras: POINT_EXTRAS }, 'R'),
  point_left: makeArmAction({ durMs: 1400, profile: POINT_PROFILE, extras: POINT_EXTRAS }, 'L'),
  hands_on_hip: {
    durMs: 1800,
    apply: (k, rt) => {
      const s = Math.sin(k * Math.PI)
      const skel = rt.getSkeleton()
      const hip = skel?.getBone('hip')?.bindWorldPos

      if (hip) {
        lerpHandIK(rt, 'L', s, hip.x - 38, hip.y + 10)
        lerpHandIK(rt, 'R', s, hip.x + 38, hip.y + 10)
      }

      rt.target.body = 0.15 * s
    }
  },
  hair_touch: {
    durMs: 1500,
    apply: (k, rt) => {
      const s = Math.sin(k * Math.PI)
      const skel = rt.getSkeleton()
      const head = skel?.getBone('head')?.bindWorldPos

      if (head) {
        lerpHandIK(rt, 'R', s, head.x + 28, head.y + 15)
      }

      rt.target.angleZ = 0.2 * s
      rt.target.eyeOpenL = 1 - 0.3 * s
      rt.target.eyeOpenR = 1 - 0.3 * s
    }
  },
  spread_arms: {
    durMs: 1500,
    apply: (k, rt) => {
      const s = Math.sin(Math.min(1, k * 1.2) * Math.PI)
      const geomL = getArmGeometry(rt, 'L')
      const geomR = getArmGeometry(rt, 'R')

      if (geomL) {
        const r = geomL.len * 0.85
        lerpHandIK(rt, 'L', s, geomL.sh.x - r * 0.95, geomL.sh.y + r * 0.25)
      }

      if (geomR) {
        const r = geomR.len * 0.85
        lerpHandIK(rt, 'R', s, geomR.sh.x + r * 0.95, geomR.sh.y + r * 0.25)
      }
    }
  },
  look_away_left: {
    durMs: 1600,
    apply: (k, rt) => {
      rt.target.eyeX = -0.85 * Math.sin(Math.min(1, k * 1.3) * Math.PI)
      rt.target.angleX = -0.35 * Math.sin(k * Math.PI)
    }
  },
  look_away_right: {
    durMs: 1600,
    apply: (k, rt) => {
      rt.target.eyeX = 0.85 * Math.sin(Math.min(1, k * 1.3) * Math.PI)
      rt.target.angleX = 0.35 * Math.sin(k * Math.PI)
    }
  },
  turn_body_left: {
    durMs: 1300,
    apply: (k, rt) => {
      rt.target.body = -Math.sin(k * Math.PI) * 0.5
      rt.target.angleX = -0.3 * Math.sin(k * Math.PI)
    }
  },
  turn_body_right: {
    durMs: 1300,
    apply: (k, rt) => {
      rt.target.body = Math.sin(k * Math.PI) * 0.5
      rt.target.angleX = 0.3 * Math.sin(k * Math.PI)
    }
  },
  lean_forward: {
    durMs: 1400,
    apply: (k, rt) => {
      rt.target.angleY = -Math.sin(k * Math.PI) * 0.4
      rt.target.body = Math.sin(k * Math.PI) * 0.2
    }
  },
  shy: {
    durMs: 2000,
    apply: (k, rt) => {
      const s = Math.sin(k * Math.PI)
      rt.target.eyeOpenL = 1 - 0.35 * s
      rt.target.eyeOpenR = 1 - 0.35 * s
      rt.target.angleZ = 0.25 * s
      rt.target.eyeCY = 0.1 * s
    }
  },
  idle_glance: {
    durMs: 1200,
    apply: (k, rt) => {
      rt.target.eyeX = Math.sin(k * Math.PI * 2) * 0.6
    }
  },
  petting: {
    durMs: 1600,
    apply: (k, rt) => {
      const s = Math.sin(k * Math.PI)
      rt.target.eyeOpenL = 1 - 0.55 * s
      rt.target.eyeOpenR = 1 - 0.55 * s
      rt.target.angleZ = 0.18 * s
    }
  },
  dizzy: {
    durMs: 2400,
    apply: (k, rt) => {
      rt.target.angleZ = Math.sin(k * Math.PI * 6) * 0.35 * (1 - k)
    }
  },
  click: {
    durMs: 800,
    apply: (k, rt) => {
      rt.target.angleY = Math.sin(k * Math.PI * 2) * 0.3
    }
  },
  long_press: {
    durMs: 1200,
    apply: (k, rt) => {
      rt.target.eyeCY = Math.sin(k * Math.PI) * 0.25
    }
  },
  drag_end: {
    durMs: 900,
    apply: (k, rt) => {
      rt.target.angleZ = Math.sin(k * Math.PI * 3) * 0.25 * (1 - k)
    }
  }
}
