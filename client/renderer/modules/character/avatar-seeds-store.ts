// 激活形象的种子图本地缓存：自备图参考图（头像种子 / 全身种子）由客户端在
// 生成、确认与水合时写入并持久化，弹窗直接读本地缓存展示。
// 原始 asset 路径入库，展示 URL 经 resolvePortraitUrl 解析。

import { atom } from 'nanostores'

import { currentClearEpoch, definePersistedAtom, registerStorageClearHandler } from '@/shared/lib/storage'

import { resolvePortraitUrl } from './avatar-image'

export interface AvatarSeeds {
  avatarId: number | null
  /** 半身头像展示 URL（asset_url），独立全身参考的自备图参考图。 */
  avatarUrl: string | null
  /** 独立全身种子图展示 URL（seed_fullbody_url），换装 / 场景的自备图参考图。 */
  fullbodySeedUrl: string | null
}

interface PersistedAvatarSeeds {
  avatarId: number | null
  /** 后端原始 asset 路径，展示前需 resolve。 */
  assetUrl: string | null
  /** 后端原始全身种子路径。 */
  fullbodySeedUrl: string | null
}

interface AvatarWire {
  asset_url?: string | null
  id?: number | null
  seed_fullbody_url?: string | null
}

interface AvatarSeedsPatch {
  avatarId?: number | null
  assetUrl?: string | null
  fullbodySeedUrl?: string | null
  /** 已解析好的展示 URL；缺省时由对应 raw 路径解析。 */
  avatarDisplayUrl?: string | null
  fullbodyDisplayUrl?: string | null
}

const EMPTY_SEEDS: AvatarSeeds = { avatarId: null, avatarUrl: null, fullbodySeedUrl: null }
const EMPTY_PERSISTED: PersistedAvatarSeeds = { avatarId: null, assetUrl: null, fullbodySeedUrl: null }

function isPersistableSeeds(val: unknown): val is PersistedAvatarSeeds {
  if (typeof val !== 'object' || val === null) {
    return false
  }

  const v = val as Partial<PersistedAvatarSeeds>

  return Boolean(v.assetUrl || v.fullbodySeedUrl)
}

const seedsPersisted = definePersistedAtom<PersistedAvatarSeeds>({
  fallback: EMPTY_PERSISTED,
  isPersistable: isPersistableSeeds,
  key: 'da.companion.avatar-seeds'
})

const initialPersisted = seedsPersisted.get()

export const $avatarSeeds = atom<AvatarSeeds>({
  avatarId: initialPersisted.avatarId,
  avatarUrl: null,
  fullbodySeedUrl: null
})

let inflight: Promise<AvatarSeeds> | null = null
/** 形象切换 / 清空时递增；在途 hydrate 与 patch 用它判活，避免写回上一形象的种子。 */
let seedEpoch = 0
/** 每次持久化写入递增；hydrate 用它检测飞行期间是否有更新，避免用旧网络结果覆盖新 patch。 */
let writeSeq = 0
let patchRequestSeq = 0
/** patch 串行队列：并发写方各自 await resolve 后按到达顺序合并字段，避免整行覆盖丢对侧种子。 */
let patchChain: Promise<void> = Promise.resolve()

function seedsComplete(seeds: AvatarSeeds): boolean {
  return Boolean(seeds.avatarUrl && seeds.fullbodySeedUrl)
}

function persistRaw(next: PersistedAvatarSeeds): void {
  writeSeq += 1
  seedsPersisted.reset()
  seedsPersisted.set(next)
}

function clearSeeds(avatarId: number | null = null): void {
  seedEpoch += 1
  inflight = null
  persistRaw({ ...EMPTY_PERSISTED, avatarId })
  $avatarSeeds.set({ ...EMPTY_SEEDS, avatarId })
}

function isStale(gen: number, epoch: number): boolean {
  return gen !== seedEpoch || epoch !== currentClearEpoch()
}

/**
 * 展示 URL 取值：显式 display 优先；raw 已清空则必须清空展示；
 * raw 仍在但解析失败时保留旧展示，避免闪断后自备图弹窗突然缺参考图。
 */
function resolveDisplayUrl(
  resolved: string | null,
  raw: string | null,
  explicitDisplay: string | null | undefined,
  previousDisplay: string | null
): string | null {
  if (explicitDisplay !== undefined) {
    return explicitDisplay
  }

  if (resolved) {
    return resolved
  }

  return raw ? previousDisplay : null
}

async function publishResolved(
  avatarId: number | null,
  raw: { assetUrl: string | null; fullbodySeedUrl: string | null },
  options?: { cacheOnly?: boolean; merge?: boolean }
): Promise<AvatarSeeds> {
  const epoch = currentClearEpoch()
  const gen = seedEpoch

  const [resolvedAvatar, resolvedFullbody] = await Promise.all([
    resolvePortraitUrl(raw.assetUrl, options),
    resolvePortraitUrl(raw.fullbodySeedUrl, options)
  ])

  if (isStale(gen, epoch)) {
    return $avatarSeeds.get()
  }

  const previous = $avatarSeeds.get()

  // merge：本地缓存解析失败时不要把已恢复的对侧展示冲成 null，留给网络补全。
  const seeds: AvatarSeeds = options?.merge
    ? {
        avatarId: avatarId ?? previous.avatarId,
        avatarUrl: resolvedAvatar ?? (raw.assetUrl ? previous.avatarUrl : null),
        fullbodySeedUrl: resolvedFullbody ?? (raw.fullbodySeedUrl ? previous.fullbodySeedUrl : null)
      }
    : { avatarId, avatarUrl: resolvedAvatar, fullbodySeedUrl: resolvedFullbody }

  $avatarSeeds.set(seeds)

  return seeds
}

async function runPatchAvatarSeeds(patch: AvatarSeedsPatch): Promise<void> {
  const gen = seedEpoch
  const epoch = currentClearEpoch()
  const snapshot = seedsPersisted.get()
  const memory = $avatarSeeds.get()

  // 若本次 patch 未变更某侧种子且内存已有展示，无需重新执行异步解析，避免闪断与冗余开销。
  const resolveAssetUrl = patch.assetUrl !== undefined ? patch.assetUrl : memory.avatarUrl ? null : snapshot.assetUrl

  const resolveFullbodyUrl =
    patch.fullbodySeedUrl !== undefined
      ? patch.fullbodySeedUrl
      : memory.fullbodySeedUrl
        ? null
        : snapshot.fullbodySeedUrl

  const avatarDisplayUrl =
    patch.avatarDisplayUrl !== undefined
      ? patch.avatarDisplayUrl
      : resolveAssetUrl
        ? await resolvePortraitUrl(resolveAssetUrl)
        : memory.avatarUrl

  const fullbodyDisplayUrl =
    patch.fullbodyDisplayUrl !== undefined
      ? patch.fullbodyDisplayUrl
      : resolveFullbodyUrl
        ? await resolvePortraitUrl(resolveFullbodyUrl)
        : memory.fullbodySeedUrl

  if (isStale(gen, epoch)) {
    return
  }

  const current = seedsPersisted.get()
  const avatarId = patch.avatarId !== undefined ? patch.avatarId : current.avatarId
  const assetUrl = patch.assetUrl !== undefined ? patch.assetUrl : current.assetUrl
  const fullbodySeedUrl = patch.fullbodySeedUrl !== undefined ? patch.fullbodySeedUrl : current.fullbodySeedUrl

  persistRaw({ avatarId, assetUrl, fullbodySeedUrl })

  $avatarSeeds.set({
    avatarId,
    avatarUrl: resolveDisplayUrl(avatarDisplayUrl, assetUrl, patch.avatarDisplayUrl, memory.avatarUrl),
    fullbodySeedUrl: resolveDisplayUrl(
      fullbodyDisplayUrl,
      fullbodySeedUrl,
      patch.fullbodyDisplayUrl,
      memory.fullbodySeedUrl
    )
  })
}

/** 生成 / 确认 / 水合路径写入已知种子：更新内存展示 URL，并持久化原始 asset 路径。 */
export function patchAvatarSeeds(patch: AvatarSeedsPatch): Promise<void> {
  patchRequestSeq += 1
  const task = patchChain.then(() => runPatchAvatarSeeds(patch))
  patchChain = task.catch(() => undefined)

  return task
}

registerStorageClearHandler(() => {
  clearSeeds(null)
})

if (initialPersisted.assetUrl || initialPersisted.fullbodySeedUrl) {
  void publishResolved(
    initialPersisted.avatarId,
    { assetUrl: initialPersisted.assetUrl, fullbodySeedUrl: initialPersisted.fullbodySeedUrl },
    { cacheOnly: true, merge: true }
  )
}

/** 形象切换时由 portrait-store 调用，清空上一形象的种子缓存。 */
export function clearAvatarSeeds(avatarId: number | null = null): void {
  clearSeeds(avatarId)
}

/**
 * 确保本地缓存可用：已有展示 URL 时直接返回；有持久化原始路径时优先本地解析；
 * 仍缺失才向 avatar 接口补拉（状态水合，不是自备图参考图下发）。
 */
export function hydrateAvatarSeeds(): Promise<AvatarSeeds> {
  if (inflight) {
    return inflight
  }

  const existing = $avatarSeeds.get()

  if (seedsComplete(existing)) {
    return Promise.resolve(existing)
  }

  const avatarId = existing.avatarId
  const persisted = seedsPersisted.get()
  const gen = seedEpoch

  const load = (async (): Promise<AvatarSeeds> => {
    try {
      if (persisted.assetUrl || persisted.fullbodySeedUrl) {
        const restored = await publishResolved(
          avatarId,
          { assetUrl: persisted.assetUrl, fullbodySeedUrl: persisted.fullbodySeedUrl },
          { cacheOnly: true, merge: true }
        )

        if (seedsComplete(restored) && !isStale(gen, currentClearEpoch())) {
          return restored
        }
      }

      const seqBeforeRequest = writeSeq
      const res = await window.spiritagent.api<AvatarWire>({ path: '/api/companion/avatar' })

      if (isStale(gen, currentClearEpoch())) {
        return $avatarSeeds.get()
      }

      const memory = $avatarSeeds.get()
      const nextAvatarId = res?.id ?? avatarId
      const assetUrl = res?.asset_url || null
      const fullbodySeedRaw = res?.seed_fullbody_url || null

      const currentPersisted = seedsPersisted.get()

      // 飞行期间已有 patch/清空且本地已完全就绪：直接返回，避免用旧网络结果冲刷更新的本地状态。
      if (
        writeSeq !== seqBeforeRequest &&
        seedsComplete(memory) &&
        currentPersisted.assetUrl &&
        currentPersisted.fullbodySeedUrl
      ) {
        return memory
      }

      // 无论飞行期间是否写入，服务端返回的种子路径在本地缺失时都必须补齐并持久化；
      // 本地有更新的 raw/展示时优先保留本地，服务端明确返回空时亦不冲掉已有展示。
      const mergedAssetUrl =
        currentPersisted.assetUrl || assetUrl || (memory.avatarUrl ? currentPersisted.assetUrl : null)

      const mergedFullbodyRaw =
        currentPersisted.fullbodySeedUrl ||
        fullbodySeedRaw ||
        (memory.fullbodySeedUrl ? currentPersisted.fullbodySeedUrl : null)

      persistRaw({ avatarId: nextAvatarId, assetUrl: mergedAssetUrl, fullbodySeedUrl: mergedFullbodyRaw })

      return await publishResolved(
        nextAvatarId,
        { assetUrl: mergedAssetUrl, fullbodySeedUrl: mergedFullbodyRaw },
        { merge: true }
      )
    } catch {
      // 网络失败不清空本地展示：提示词已按参考图锚定，清空会导致自备图弹窗缺图。
      return $avatarSeeds.get()
    } finally {
      if (gen === seedEpoch) {
        inflight = null
      }
    }
  })()

  inflight = load

  return load
}

/** 角色卡事件后的服务端刷新：即使本地两张图都已缓存，也以最新已采纳种子替换旧路径。 */
export async function refreshAvatarSeeds(): Promise<void> {
  const gen = seedEpoch
  const epoch = currentClearEpoch()
  const seq = writeSeq
  const requestSeq = patchRequestSeq
  const response = await window.spiritagent.api<AvatarWire>({ path: '/api/companion/avatar' })

  if (isStale(gen, epoch) || writeSeq !== seq || patchRequestSeq !== requestSeq || !response?.id) {
    return
  }

  const currentId = seedsPersisted.get().avatarId

  if (currentId != null && response.id !== currentId) {
    return
  }

  await patchAvatarSeeds({
    avatarId: response.id,
    assetUrl: response.asset_url || null,
    fullbodySeedUrl: response.seed_fullbody_url || null
  })
}
