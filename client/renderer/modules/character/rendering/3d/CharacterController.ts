import { clamp } from '@runtime'
import * as THREE from 'three'

import type { SpriteEmotion, SpriteStateName } from '@/modules/character'
import { log } from '@/shared/lib/log'

import { type ClipMap, resolveClip } from './AnimationMap'
import { disposeThreeResources, type GltfLease, hasGltf, stashGltf, takeGltfClone } from './gltf-instance-cache'
import { createGLTFLoader } from './gltf-loader-factory'
import { $availableClipNames } from './model-store'
import { type LoadedModelInfo } from './types'

interface ProcParts {
  body: THREE.Mesh
  crackMats?: THREE.LineBasicMaterial[]
  cracks?: THREE.Line[]
  group: THREE.Group
  leftEye: THREE.Mesh
  mouth: THREE.Mesh
  rightEye: THREE.Mesh
}

/**
 * 透明地解压经过 Gzip / Deflate 压缩的 GLB 缓冲。
 * 在大幅缩小传输体积的同时，保留 100% 的网格分辨率与精度。
 */
async function decompressGlbIfNeeded(buffer: ArrayBuffer): Promise<ArrayBuffer> {
  const bytes = new Uint8Array(buffer)

  // Gzip 魔数（0x1f, 0x8b）
  if (bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
    if (typeof DecompressionStream !== 'undefined') {
      try {
        const ds = new DecompressionStream('gzip')
        const decompressed = await new Response(new Response(bytes).body?.pipeThrough(ds)).arrayBuffer()

        return decompressed
      } catch (err) {
        log.warn('CharacterController', 'Failed to decompress gzip glb buffer:', err)
      }
    }
  }

  // Deflate / zlib 魔数（0x78 0x9c / 0x78 0x01 / 0x78 0xda）
  if (bytes.length >= 2 && bytes[0] === 0x78 && (bytes[1] === 0x9c || bytes[1] === 0x01 || bytes[1] === 0xda)) {
    if (typeof DecompressionStream !== 'undefined') {
      try {
        const ds = new DecompressionStream('deflate')
        const decompressed = await new Response(new Response(bytes).body?.pipeThrough(ds)).arrayBuffer()

        return decompressed
      } catch (err) {
        log.warn('CharacterController', 'Failed to decompress deflate glb buffer:', err)
      }
    }
  }

  return buffer
}

const QUAT = new THREE.Quaternion()
const EULER = new THREE.Euler()

export class CharacterController {
  root = new THREE.Group()
  private mixer: THREE.AnimationMixer | null = null
  private clips = new Map<string, THREE.AnimationClip>()
  private actions = new Map<string, THREE.AnimationAction>()
  private actionNames = new Set<string>()
  private clipMap: ClipMap = {}
  private currentAction: THREE.AnimationAction | null = null
  private isProcedural = false
  private proc: ProcParts | null = null
  private rigType: string = 'biped'
  private headBone: THREE.Bone | null = null
  private neckBone: THREE.Bone | null = null
  private activeGltfLease: GltfLease | null = null
  onPoseChange?: () => void

  get isBipedRig(): boolean {
    return this.rigType === 'biped'
  }

  private currentState: SpriteStateName = 'idle'
  private breathPhase = 0
  private lookX = 0
  private lookY = 0
  private boneRestQuats = new Map<string, THREE.Quaternion>()

  constructor() {}

  private getAction(name: string): THREE.AnimationAction | null {
    if (!this.mixer) {
      return null
    }

    let action = this.actions.get(name)

    if (!action) {
      const clip = this.clips.get(name)

      if (clip) {
        action = this.mixer.clipAction(clip)
        this.actions.set(name, action)
      }
    }

    return action ?? null
  }

  /** 解析预取的 GLB 字节与动画；出错时回退到程序化形象。字节来自渲染进程的 `apiAssetBuffer` IPC（主进程已剥离 host 并重写 base，无 CORS 预检）。若提供了 `contentHash` 且 `gltf-instance-cache` 中已有解析好的模板，则取出深克隆而非重新解析。 */
  async load(
    bytes: ArrayBuffer | null,
    scene: THREE.Scene,
    rigType: string = 'biped',
    contentHash?: string
  ): Promise<LoadedModelInfo> {
    this.rigType = rigType

    if (bytes || (contentHash && hasGltf(contentHash))) {
      try {
        this.disposeRoot(scene)

        let rootScene: THREE.Group
        let gltfAnimations: THREE.AnimationClip[]

        if (contentHash && hasGltf(contentHash)) {
          const cached = takeGltfClone(contentHash)

          if (cached) {
            rootScene = cached.scene
            gltfAnimations = cached.animations
            this.activeGltfLease = cached
          } else {
            rootScene = new THREE.Group()
            gltfAnimations = []
            this.activeGltfLease = null
          }
        } else {
          if (!bytes) {
            throw new Error('bytes missing when gltf cache missed')
          }

          const decompressedBytes = await decompressGlbIfNeeded(bytes)
          const loader = createGLTFLoader()
          const gltf = await loader.parseAsync(decompressedBytes, '')

          // 模板由缓存持有；后续取出深克隆并递增引用计数
          if (contentHash) {
            stashGltf(contentHash, gltf.scene, gltf.animations, bytes.byteLength)
            const cloned = takeGltfClone(contentHash)

            if (cloned) {
              rootScene = cloned.scene
              gltfAnimations = cloned.animations
              this.activeGltfLease = cloned
            } else {
              rootScene = gltf.scene
              gltfAnimations = gltf.animations
              this.activeGltfLease = null
            }
          } else {
            rootScene = gltf.scene
            gltfAnimations = gltf.animations
            this.activeGltfLease = null
          }
        }

        this.root = rootScene
        this.root.traverse(child => {
          if (child instanceof THREE.Mesh) {
            child.castShadow = true
            child.receiveShadow = true
          }
        })
        scene.add(this.root)
        this.mixer = new THREE.AnimationMixer(this.root)
        this.mixer.addEventListener('finished', () => {
          if (this.mixer && !this.isProcedural) {
            const baseClip = resolveClip(this.currentState, this.clipMap, this.actionNames)

            if (baseClip) {
              this.playClip(baseClip, 0.3)
            }
          }
        })

        this.clips.clear()
        this.actions.clear()

        for (const clip of gltfAnimations) {
          this.clips.set(clip.name, clip)
        }

        this.headBone = null
        this.neckBone = null
        this.boneRestQuats.clear()
        this.root.traverse(child => {
          if (child instanceof THREE.Bone) {
            // 供应商必须按 SPEC.md §3 输出零前缀 GLB。
            this.boneRestQuats.set(child.name, child.quaternion.clone())

            if (child.name === 'Head') {
              this.headBone = child
            } else if (child.name === 'Neck' || (child.name === 'NeckTwist01' && !this.neckBone)) {
              // spec=tripo 的 biped 层级里没有 Neck，颈段是 NeckTwist01 → NeckTwist02。
              this.neckBone = child
            }
          }
        })

        this.actionNames = new Set(this.clips.keys())
        $availableClipNames.set(new Set(this.actionNames))
        this.applyState(this.currentState, null)

        return {
          clipNames: [...this.clips.keys()],
          hasAnimations: this.clips.size > 0,
          procedural: false
        }
      } catch (err) {
        log.warn('character', 'GLB load failed, using procedural fallback:', err)
      }
    }

    this.createProcedural(scene)

    return { clipNames: [], hasAnimations: false, procedural: true }
  }

  private disposeRoot(scene: THREE.Scene | null): void {
    this.headBone = null
    this.neckBone = null

    if (this.root.parent) {
      scene?.remove(this.root)
    }

    this.mixer?.stopAllAction()
    this.mixer = null
    this.clips.clear()
    this.actions.clear()
    this.actionNames.clear()

    if (this.proc?.cracks) {
      this.proc.cracks.forEach(c => c.geometry?.dispose())
    }

    if (this.proc?.crackMats) {
      this.proc.crackMats.forEach(m => m.dispose())
    }

    this.isProcedural = false
    this.proc = null
    this.boneRestQuats.clear()

    if (this.activeGltfLease) {
      // 活跃实例归还所属模板代际；共享 GPU 资源由模板 Cache 负责管理与释放
      this.activeGltfLease.release()
      this.activeGltfLease = null
    } else {
      // 未被模板缓存管理的独占资源（如程序化形象或无 hash 临时模型）在此释放
      disposeThreeResources(this.root)
    }

    this.root = new THREE.Group()
  }

  /** 运行时更新语义映射（不重载模型）。 */
  setClipMap(clipMap: ClipMap): void {
    this.clipMap = clipMap
    this.applyState(this.currentState, null)
  }

  applyState(
    state: SpriteStateName,
    emotion: SpriteEmotion | null,
    opts?: {
      clipOverride?: string | null
      action?: string | null
    }
  ): void {
    this.currentState = state

    if (this.isProcedural) {
      return
    }

    const available = this.actionNames

    if (opts?.clipOverride && available.has(opts.clipOverride)) {
      this.playClip(opts.clipOverride, 0.25)

      return
    }

    // 自主视觉推理给出的 action 与 emotion 都是映射表里的一等语义键，命中即单次播放。
    for (const key of [opts?.action, emotion]) {
      const oneShot = key ? resolveClip(key, this.clipMap, available) : null

      if (oneShot && this.playOnce(oneShot, 0.25)) {
        return
      }
    }

    const targetClipName = resolveClip(state, this.clipMap, available)

    if (targetClipName) {
      this.playClip(targetClipName, 0.35)
    }
  }

  applyEmotion(emotion: SpriteEmotion | null): void {
    this.applyState(this.currentState, emotion)
  }

  /** 空间移动肢体动画（DESIGN §3.3「移动本身是动画」）：walk/walk_fast 移动时
   *  播放 walk clip（绝大多数 rig 的映射表都含 walk），still 恢复当前状态 clip。 */
  playLocomotion(clipKey: string | null): void {
    if (this.isProcedural) {
      return
    }

    if (clipKey) {
      const clip = resolveClip(clipKey, this.clipMap, this.actionNames)

      if (clip) {
        this.playClip(clip, 0.3)

        return
      }
    }

    this.applyState(this.currentState, null)
  }

  /** 单次播放并在结束后由 mixer 的 finished 回调切回基础状态；返回是否真的播了。 */
  private playOnce(name: string, fade: number): boolean {
    const act = this.getAction(name)

    if (!act) {
      return false
    }

    act.reset().setLoop(THREE.LoopOnce, 1)
    act.clampWhenFinished = true
    this.currentAction?.crossFadeTo(act, fade, false)
    act.play()
    this.currentAction = act
    this.onPoseChange?.()

    return true
  }

  setLipSyncAmplitude(amp: number): void {
    if (this.isProcedural && this.proc) {
      this.proc.mouth.scale.y = 1 + amp * 3.5
    }
  }

  setBlinkBlocked(blocked: boolean): void {
    if (this.isProcedural && this.proc) {
      if (blocked) {
        this.proc.leftEye.scale.y = 1
        this.proc.rightEye.scale.y = 1
      }
    }
  }

  setLookTarget(nx: number, ny: number): void {
    // nx, ny 从屏幕中心归一化到 [-1, 1]
    this.lookX = clamp(nx, -1, 1)
    this.lookY = clamp(ny, -1, 1)
  }

  private dragTilt = { x: 0, z: 0 }

  setDragVelocity(vx: number, vy: number): void {
    // vx, vy 以 px/ms 为单位归一化
    this.dragTilt.z = clamp(-vx * 0.12, -0.25, 0.25)
    this.dragTilt.x = clamp(vy * 0.08, -0.2, 0.2)
  }

  update(delta: number): void {
    this.breathPhase += delta
    this.mixer?.update(delta)

    this.dragTilt.x = THREE.MathUtils.lerp(this.dragTilt.x, 0, 0.1)
    this.dragTilt.z = THREE.MathUtils.lerp(this.dragTilt.z, 0, 0.1)

    if (this.isProcedural) {
      this.updateProcedural(delta)
    } else {
      // GLB 角色若内置动作不包含 idle 浮动，则手动添加细微的 idle 浮动
      this.root.position.y = Math.sin(this.breathPhase * 0.8) * 0.01
    }

    this.applyLookAt()
  }

  dispose(): void {
    this.disposeRoot(null)
  }

  private playClip(name: string, fade: number): void {
    const next = this.getAction(name)

    if (!next) {
      return
    }

    if (next === this.currentAction && next.isRunning()) {
      return
    }

    next.reset().setLoop(THREE.LoopRepeat, Infinity).setEffectiveWeight(1).setEffectiveTimeScale(1)
    next.clampWhenFinished = false
    this.currentAction?.crossFadeTo(next, fade, false)
    next.play()
    this.currentAction = next
    this.onPoseChange?.()
  }

  private applyLookAt(): void {
    // 身体向光标方向微微偏航
    const yaw = this.lookX * 0.12
    this.root.rotation.y = THREE.MathUtils.lerp(this.root.rotation.y, yaw, 0.08)

    // 仅在主动拖拽期间保留拖拽惯性（静止时平滑衰减为 0）
    const pitch = this.dragTilt.x * 0.4
    const roll = this.dragTilt.z * 0.4
    this.root.rotation.x = THREE.MathUtils.lerp(this.root.rotation.x, pitch, 0.1)
    this.root.rotation.z = THREE.MathUtils.lerp(this.root.rotation.z, roll, 0.1)

    // 微微下颌内收 + 光标视线追踪，营造自然、有交流感的平视眼神。
    // 人像摄影里约 3° 的下颌内收能让下颌线更柔和、视线更聚焦，
    // 避免 AI 原始骨骼那种"仰头看天花板"的脱节感。
    if (this.headBone && this.isBipedRig) {
      // 有动作在播时 mixer 每帧已写入该骨骼的绝对四元数，直接叠加即可（不会累加溢出）；
      // 无动作在播时 mixer 不碰骨骼，必须以静止姿势为基准，否则逐帧相乘会让头部旋转发散。
      const animated = this.currentAction?.isRunning() ?? false
      const chinTuckPitch = 0.05
      const lookPitch = -this.lookY * 0.06
      const lookYaw = this.lookX * 0.1

      EULER.set(chinTuckPitch + lookPitch, lookYaw, 0, 'YXZ')
      QUAT.setFromEuler(EULER)

      const restHead = this.boneRestQuats.get(this.headBone.name)

      if (!animated && restHead) {
        this.headBone.quaternion.copy(restHead)
      }

      this.headBone.quaternion.multiply(QUAT)

      if (this.neckBone) {
        const restNeck = this.boneRestQuats.get(this.neckBone.name)
        EULER.set((chinTuckPitch + lookPitch) * 0.25, lookYaw * 0.25, 0, 'YXZ')
        QUAT.setFromEuler(EULER)

        if (!animated && restNeck) {
          this.neckBone.quaternion.copy(restNeck)
        }

        this.neckBone.quaternion.multiply(QUAT)
      }
    }
  }

  // 兜底形象：onboarding 引导期（尚无形象资产）以及 2D/3D 资产生成中或彻底不可用时撑住画面（不变量 #10）。

  private createProcedural(scene: THREE.Scene): void {
    this.isProcedural = true
    const group = new THREE.Group()

    const bodyGeo = new THREE.SphereGeometry(0.5, 48, 48)

    const bodyMat = new THREE.MeshStandardMaterial({
      color: 0xfff4d6,
      emissive: 0x332200,
      emissiveIntensity: 0.15,
      roughness: 0.55,
      metalness: 0.0
    })

    const body = new THREE.Mesh(bodyGeo, bodyMat)
    body.scale.set(0.82, 1.08, 0.82)
    body.position.y = 1.0
    body.castShadow = true
    body.receiveShadow = true
    group.add(body)

    const eyeGeo = new THREE.SphereGeometry(0.055, 20, 20)
    const eyeMat = new THREE.MeshStandardMaterial({ color: 0x1a1a2e, roughness: 0.12 })
    const leftEye = new THREE.Mesh(eyeGeo, eyeMat)
    leftEye.position.set(-0.13, 1.18, 0.38)
    group.add(leftEye)
    const rightEye = new THREE.Mesh(eyeGeo, eyeMat.clone())
    rightEye.position.set(0.13, 1.18, 0.38)
    group.add(rightEye)

    const mouthGeo = new THREE.BoxGeometry(0.1, 0.015, 0.02)
    const mouthMat = new THREE.MeshStandardMaterial({ color: 0xc89060, roughness: 0.4 })
    const mouth = new THREE.Mesh(mouthGeo, mouthMat)
    mouth.position.set(0, 1.04, 0.4)
    group.add(mouth)

    // 蛋壳表面的预制裂纹线装饰
    const cracks: THREE.Line[] = []
    const crackMats: THREE.LineBasicMaterial[] = []

    const crackPathsPoints = [
      // 裂纹 1：左上
      [
        new THREE.Vector3(-0.15, 1.3, 0.42),
        new THREE.Vector3(-0.25, 1.22, 0.38),
        new THREE.Vector3(-0.2, 1.12, 0.43),
        new THREE.Vector3(-0.32, 1.05, 0.35)
      ],
      // 裂纹 2：中右
      [
        new THREE.Vector3(0.25, 1.1, 0.4),
        new THREE.Vector3(0.35, 1.0, 0.32),
        new THREE.Vector3(0.28, 0.9, 0.39),
        new THREE.Vector3(0.38, 0.82, 0.28)
      ],
      // 裂纹 3：左下
      [new THREE.Vector3(-0.2, 0.85, 0.42), new THREE.Vector3(-0.28, 0.78, 0.36), new THREE.Vector3(-0.18, 0.7, 0.41)]
    ]

    crackPathsPoints.forEach(pts => {
      const geo = new THREE.BufferGeometry().setFromPoints(pts)

      const mat = new THREE.LineBasicMaterial({
        color: 0xffd166,
        transparent: true,
        opacity: 0.4
      })

      const crackLine = new THREE.Line(geo, mat)
      group.add(crackLine)
      cracks.push(crackLine)
      crackMats.push(mat)
    })

    this.proc = { body, leftEye, rightEye, mouth, cracks, crackMats, group }
    this.root = group
    scene.add(group)
  }

  private updateProcedural(_delta: number): void {
    if (!this.proc) {
      return
    }

    const t = this.breathPhase

    // 3.4s emissive breathing cycle matching login-glow and installer glow
    const reducedMotion =
      typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches

    const breath = reducedMotion ? 0.5 : Math.sin((t * 2 * Math.PI) / 3.4) * 0.5 + 0.5
    const bodyMat = this.proc.body.material as THREE.MeshStandardMaterial

    if (reducedMotion) {
      bodyMat.emissiveIntensity = 0.35
    } else {
      bodyMat.emissiveIntensity = 0.15 + 0.4 * breath

      if (this.proc.crackMats && this.proc.crackMats.length > 0) {
        const cycle = t % 12
        const activeIdx = Math.floor(t / 12) % this.proc.crackMats.length
        this.proc.crackMats.forEach((mat, idx) => {
          if (idx === activeIdx && cycle < 0.6) {
            const flashWave = Math.sin((cycle / 0.6) * Math.PI)
            mat.opacity = 0.4 + 0.5 * flashWave
          } else {
            mat.opacity = 0.4
          }
        })
      }
    }

    // 每帧重置变换矩阵——所有 case 共用的「基线」重置必须在 switch 之前，
    // 防止 disconnected 写入的 body.rotation.x / mouth.scale.x 在切到 idle/speaking 时残留。
    this.proc.group.position.y = 0
    this.proc.body.scale.set(0.82, 1.08, 0.82)
    this.proc.body.rotation.x = 0
    this.proc.body.rotation.z = 0
    this.proc.mouth.scale.y = 1
    this.proc.mouth.scale.x = 1

    const baseEyeY = 1

    switch (this.currentState) {
      case 'speaking': {
        this.proc.group.position.y = Math.sin(t * 5) * 0.015

        // 嘴型缩放由 setLipSyncAmplitude 驱动，而非正弦波
        break
      }

      case 'thinking': {
        this.proc.body.rotation.z = Math.sin(t * 0.8) * 0.08

        break
      }

      case 'working': {
        this.proc.group.position.y = Math.sin(t * 3) * 0.008

        break
      }

      case 'interacting': {
        this.proc.group.position.y = Math.abs(Math.sin(t * 4)) * 0.06

        break
      }

      case 'disconnected': {
        // DESIGN §6.5「云端（脑）断连」：打哈欠 + 歪头发呆 + 犯困眯眼。
        this.proc.body.rotation.z = -0.12
        this.proc.body.rotation.x = Math.sin(t * 0.6) * 0.06
        // 张嘴哈欠（5s 周期，前 1.5s 张开到最大，后 3.5s 缓慢闭合）
        const yawnPhase = (t * 0.2) % 1
        const yawnAmount = yawnPhase < 0.3 ? yawnPhase / 0.3 : Math.max(0, 1 - (yawnPhase - 0.3) / 0.7)
        this.proc.mouth.scale.y = 1 + yawnAmount * 4
        this.proc.mouth.scale.x = 1 + yawnAmount * 0.5
        // 犯困：眼睛基线压到 0.4，叠加慢周期呼吸；return 跳过下面 blink 重写
        this.proc.leftEye.scale.y = 0.4 + Math.sin(t * 0.3) * 0.1
        this.proc.rightEye.scale.y = this.proc.leftEye.scale.y

        return
      }
    }

    // 程序化眨眼——disconnected 已在 case 内 return 跳过本段。
    const blinkCycle = t % (3 + (this.currentState.charCodeAt(0) % 3))
    const blinkWindow = blinkCycle > 2.8 && blinkCycle < 2.95
    const eyeScaleY = blinkWindow ? 0.1 : baseEyeY
    this.proc.leftEye.scale.y = eyeScaleY
    this.proc.rightEye.scale.y = eyeScaleY
  }
}
