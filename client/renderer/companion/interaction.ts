import { $availableClipNames, $clipMap, resolveClip } from '@/3d'
import { $gateway } from '@/shared/store/gateway'
import type { ReactionBucket } from '@/shared/types/reactions'

import { $lastIdleSeconds, reportInteractionStat } from './activity'
import {
  $clipOverride,
  $spriteAction,
  $spriteEmotion,
  playSpriteActionSequence,
  setSpriteState
} from './companion-store'
import { $personalityTags } from './persona-store'
import { $llmReactions } from './prefs'
import { pickReaction, playReactionAudio } from './reactions/reaction-audio'
import { emitVfx } from './vfx'

export type NormalizedRegion = 'head' | 'body' | 'item'

const HEAD_REGIONS = new Set(['head', 'hair', 'hair_front', 'bangs', 'ear', 'face', 'back_hair', 'front_hair'])

const ITEM_REGIONS = new Set(['held', 'item', 'accessory'])

export function normalizeRegion(rawRegion?: string): NormalizedRegion {
  if (!rawRegion) {
    return 'body'
  }

  const lower = rawRegion.toLowerCase().trim()

  if (HEAD_REGIONS.has(lower)) {
    return 'head'
  }

  if (ITEM_REGIONS.has(lower)) {
    return 'item'
  }

  return 'body'
}

let lastPokeTime = 0
let pokeCount = 0
let resetTimer: ReturnType<typeof setTimeout> | null = null
let lastLlmPokeAt = 0
let inPokeWindow = false
let pokeWindowTimer: ReturnType<typeof setTimeout> | null = null

function bucketForPokeCount(): ReactionBucket {
  if (pokeCount >= 5) {
    return 'poke-heavy'
  }

  if (pokeCount >= 3) {
    return 'poke-medium'
  }

  return 'poke-light'
}

interface InteractRpcResponse {
  text?: string | null
  emotion?: string | null
  reason?: string
}

interface InteractRpcRequest {
  kind: 'poke' | 'pet' | 'dizzy'
  poke_count: number
  idle_seconds: number
  local_hour: number
  /** 2D 路径子区域命中（head/face/arm_L/arm_R/body/back_hair/front_hair/skirt）；
   *  不传 = 整精灵矩形命中。3D 路径走 silhouette hit，2D 路径由 2D 渲染层 hitmap 提供。 */
  region?: string
}

const LLM_INTERACT_COOLDOWN_MS = 5 * 60 * 1000

function playLocalReaction(bucket: ReactionBucket, tags: string[]): void {
  const entry = pickReaction(bucket, tags)

  void playReactionAudio(entry)
}

async function triggerReaction(
  bucket: ReactionBucket,
  tags: string[],
  region?: string,
  kind: 'poke' | 'pet' | 'dizzy' = 'poke'
): Promise<void> {
  const now = Date.now()
  const useLlm = $llmReactions.get()
  const inLlmCooldown = now - lastLlmPokeAt < LLM_INTERACT_COOLDOWN_MS

  if (!useLlm || inLlmCooldown) {
    playLocalReaction(bucket, tags)

    return
  }

  // 乐观抢占：下方失败时退还配额，避免一次瞬时错误锁死 5 分钟。
  lastLlmPokeAt = now

  const gateway = $gateway.get()

  if (!gateway) {
    lastLlmPokeAt = 0
    playLocalReaction(bucket, tags)

    return
  }

  try {
    const request: InteractRpcRequest = {
      kind,
      poke_count: pokeCount,
      idle_seconds: Math.max(0, $lastIdleSeconds.get()),
      local_hour: new Date().getHours()
    }

    if (region) {
      request.region = region
    }

    const res = await gateway.request<InteractRpcResponse>('companion.interact', { ...request })

    // 服务端成本窗口同步到本地时钟，避免立刻重新打满。
    if (res?.reason === 'rate_limited') {
      lastLlmPokeAt = Date.now()
      playLocalReaction(bucket, tags)

      return
    }

    if (res?.text) {
      if (res.emotion) {
        $spriteEmotion.set(res.emotion)
      }

      void playReactionAudio({ id: `llm-${kind}`, text: res.text, tags: [], bucket })

      return
    }

    lastLlmPokeAt = 0
  } catch {
    lastLlmPokeAt = 0
  }

  playLocalReaction(bucket, tags)
}

export function handlePetInteraction(nx = 0.5, ny = 0.25): void {
  const tags = $personalityTags.get()

  emitVfx('heart', { nx, ny, count: 2 })
  $clipOverride.set('petting')
  $spriteAction.set('petting')
  $spriteEmotion.set('happy')
  setSpriteState('interacting', { durationMs: 2500 })

  void triggerReaction('poke-light', tags, 'head', 'pet')
  reportInteractionStat('poke')
}

export function handleDizzyInteraction(): void {
  const tags = $personalityTags.get()

  emitVfx('dizzy_stars')
  $clipOverride.set('dizzy')
  $spriteAction.set('dizzy')
  $spriteEmotion.set('confused')
  setSpriteState('interacting', { durationMs: 3000 })

  void triggerReaction('poke-heavy', tags, undefined, 'dizzy')
  reportInteractionStat('poke')
}

export function isPokeActive(): boolean {
  return inPokeWindow
}

export function handleLongPressBodyInteraction(region?: string): void {
  inPokeWindow = true
  // 400ms 触感/微颤反馈（spec §4.3 & §13）
  $spriteAction.set('tremor')
  handlePokeInteraction(region)
}

export function playAffectionateAction(): void {
  // 亲昵动作序列（spec §4.2 & §4.3）
  emitVfx('heart', { nx: 0.5, ny: 0.25, count: 2 })
  $clipOverride.set('petting')
  playSpriteActionSequence(['turn_towards', 'nod'])
  setSpriteState('interacting', { durationMs: 2200 })
}

export function handlePokeInteraction(region?: string): void {
  const now = Date.now()

  if (now - lastPokeTime < 3000) {
    pokeCount += 1
  } else {
    pokeCount = 1
  }

  lastPokeTime = now
  inPokeWindow = true

  if (pokeWindowTimer) {
    clearTimeout(pokeWindowTimer)
  }

  pokeWindowTimer = setTimeout(() => {
    inPokeWindow = false
    pokeCount = 0
    pokeWindowTimer = null
  }, 3000)

  if (resetTimer) {
    clearTimeout(resetTimer)
  }

  resetTimer = setTimeout(() => {
    pokeCount = 0
  }, 4000)

  // 连戳阈值分流
  if (pokeCount >= 8) {
    handleDizzyInteraction()

    return
  }

  if (pokeCount >= 5) {
    emitVfx('anger', { nx: 0.5, ny: 0.2 })
  }

  const tags = $personalityTags.get()
  const bucket = bucketForPokeCount()

  // 音效资产按 kebab 分档，线上语义键统一 snake 且不分档。
  $clipOverride.set(resolveClip('poke', $clipMap.get(), $availableClipNames.get()))
  setSpriteState('interacting', { durationMs: 2000 })

  void triggerReaction(bucket, tags, region, 'poke')
  reportInteractionStat('poke')
}

export function handleDragEndInteraction(): void {
  playLocalReaction('drag', $personalityTags.get())
}
