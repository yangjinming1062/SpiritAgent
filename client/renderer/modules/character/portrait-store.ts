import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, definePersistedAtom, registerStorageClearHandler } from '@/shared/lib/storage'

import { resolvePortraitUrl } from './avatar-image'
import { clearAvatarSeeds, hydrateAvatarSeeds, patchAvatarSeeds } from './avatar-seeds-store'

interface PersistedPortrait {
  assetUrl: string | null
  avatarId: number | null
}

const DEFAULT_PORTRAIT: PersistedPortrait = {
  assetUrl: null,
  avatarId: null
}

function isPersistablePortrait(val: unknown): val is PersistedPortrait {
  if (typeof val !== 'object' || val === null) {
    return false
  }

  const v = val as Partial<PersistedPortrait>

  return typeof v.assetUrl === 'string' && Boolean(v.assetUrl)
}

function portraitAssetIdentity(url: string | null | undefined): string {
  if (!url) {
    return ''
  }

  try {
    return new URL(url, 'http://127.0.0.1').pathname
  } catch {
    return url
  }
}

const portraitPersisted = definePersistedAtom<PersistedPortrait>({
  fallback: DEFAULT_PORTRAIT,
  isPersistable: isPersistablePortrait,
  key: 'da.companion.portrait'
})

const initialPersisted = portraitPersisted.get()

export const $portraitUrl = atom<string | null>(null)

// 当前 avatar 行 id——由 hydrate 与每次创建新行的重生写入。
// 服务端读取当前 avatar 行，这里只是为画廊选择做镜像。
export const $activeAvatarId = atom<number | null>(initialPersisted.avatarId)

// 用户在按「重新生成」之前输入的反馈文本。在所有暴露重生流程的面板间共享
// （onboarding / 伙伴设置 / 重新对话微调性格 / 角色 inline 编辑），
// 这样用户在某个面板里输入了一半再切到另一个面板时草稿不会丢。
// 每次重生成功后由 useRegeneratePortrait 清掉。
export const $regenFeedback = atom<string>('')

export interface PortraitEntry {
  assetUrl?: string | null
  avatarId: number | null
  portraitUrl: string | null
}

const MAX_HISTORY = 5

export const $portraitHistory = atom<PortraitEntry[]>([])
export const $portraitSelectedIdx = atom<number>(0)

registerStorageClearHandler(() => {
  $portraitUrl.set(null)
  $activeAvatarId.set(null)
  $portraitHistory.set([])
  $portraitSelectedIdx.set(0)
})

function persistPortrait(next: PersistedPortrait): void {
  const previousId = $activeAvatarId.get()

  $activeAvatarId.set(next.avatarId)

  if (previousId !== next.avatarId) {
    clearAvatarSeeds(next.avatarId)
  }

  portraitPersisted.reset()
  portraitPersisted.set({ assetUrl: next.assetUrl, avatarId: next.avatarId })
}

async function restorePortraitFromDisk(assetUrl: string, epoch: number): Promise<void> {
  const restored = await resolvePortraitUrl(assetUrl, { cacheOnly: true })

  if (!restored || currentClearEpoch() !== epoch || $portraitUrl.get()) {
    return
  }

  $portraitUrl.set(restored)
}

if (initialPersisted.assetUrl) {
  void restorePortraitFromDisk(initialPersisted.assetUrl, currentClearEpoch())
}

interface PortraitUrls {
  assetUrl?: string | null
  seedFrontUrl?: string | null
  seedBackUrl?: string | null
  id?: number | null
}

export async function applyPortrait(
  urls: PortraitUrls
): Promise<{ avatar: string | null; seedBack: string | null; seedFront: string | null }> {
  const epoch = currentClearEpoch()
  const avatar = urls.assetUrl === undefined ? null : await resolvePortraitUrl(urls.assetUrl)
  const seedFront = urls.seedFrontUrl === undefined ? null : await resolvePortraitUrl(urls.seedFrontUrl)
  const seedBack = urls.seedBackUrl === undefined ? null : await resolvePortraitUrl(urls.seedBackUrl)

  if (currentClearEpoch() !== epoch) {
    return { avatar: null, seedBack: null, seedFront: null }
  }

  if (avatar) {
    $portraitUrl.set(avatar)
    persistPortrait({
      assetUrl: urls.assetUrl ?? portraitPersisted.get().assetUrl,
      avatarId: urls.id ?? $activeAvatarId.get()
    })
    void patchAvatarSeeds({
      avatarId: urls.id ?? $activeAvatarId.get(),
      assetUrl: urls.assetUrl ?? undefined,
      avatarDisplayUrl: avatar
    })
  } else if (urls.id != null) {
    const previousId = $activeAvatarId.get()

    $activeAvatarId.set(urls.id)

    // 头像 URL 解析失败但 id 已切换：仍须作废上一形象的种子缓存，避免自备图参考图串号。
    if (previousId !== urls.id) {
      clearAvatarSeeds(urls.id)
    }
  }

  return { avatar, seedBack, seedFront }
}

export async function hydratePortrait(): Promise<void> {
  const epoch = currentClearEpoch()

  try {
    const res = await window.spiritagent.api<{
      asset_url?: string
      id?: number
    }>({
      path: '/api/companion/avatar'
    })

    if (currentClearEpoch() !== epoch) {
      return
    }

    if (!res || !res.asset_url) {
      return
    }

    const currentCached = portraitPersisted.get()

    const sameIdentity =
      currentCached.avatarId === res.id &&
      portraitAssetIdentity(currentCached.assetUrl) === portraitAssetIdentity(res.asset_url)

    if (sameIdentity) {
      if (!$portraitUrl.get() && currentCached.assetUrl) {
        await restorePortraitFromDisk(currentCached.assetUrl, epoch)
      }

      if (res.id != null && $activeAvatarId.get() !== res.id) {
        const previousId = $activeAvatarId.get()

        $activeAvatarId.set(res.id)

        // 缓存身份与服务端一致但本地 active id 漂移：纠正 id 时同步作废旧形象种子。
        if (previousId !== res.id) {
          clearAvatarSeeds(res.id)
        }
      }

      return
    }

    const newAvatar = await resolvePortraitUrl(res.asset_url)

    if (currentClearEpoch() !== epoch) {
      return
    }

    if (newAvatar) {
      $portraitUrl.set(newAvatar)
      persistPortrait({
        assetUrl: res.asset_url,
        avatarId: res.id ?? null
      })
      void patchAvatarSeeds({
        avatarId: res.id ?? null,
        assetUrl: res.asset_url,
        avatarDisplayUrl: newAvatar
      })
    } else {
      log.warn('portrait', 'hydratePortrait failed to resolve new avatar; keeping existing portrait')
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('portrait', 'hydratePortrait failed', error)
    }
  }
}

export async function hydratePortraitHistory(): Promise<void> {
  const epoch = currentClearEpoch()

  try {
    const res = await window.spiritagent.api<{
      history: Array<{
        asset_url: string
        id: number
      }>
    }>({
      path: '/api/companion/avatar/history'
    })

    if (currentClearEpoch() !== epoch) {
      return
    }

    const items = [...(res?.history ?? [])].reverse()
    const previousById = new Map($portraitHistory.get().map(entry => [entry.avatarId, entry]))

    const entries = await Promise.all(
      items.map(async item => {
        const portraitUrl = await resolvePortraitUrl(item.asset_url)
        const previous = previousById.get(item.id)

        return {
          assetUrl: item.asset_url,
          avatarId: item.id,
          portraitUrl: portraitUrl ?? previous?.portraitUrl ?? null
        }
      })
    )

    if (currentClearEpoch() !== epoch) {
      return
    }

    if (entries.some(entry => entry.portraitUrl) || $portraitHistory.get().length === 0) {
      $portraitHistory.set(entries)
    }

    const activeId = $activeAvatarId.get()

    if (activeId != null) {
      const activeIdx = entries.findIndex(entry => entry.avatarId === activeId)

      if (activeIdx >= 0) {
        $portraitSelectedIdx.set(activeIdx)
      }
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('portrait', 'hydratePortraitHistory failed', error)
    }
  }
}

export async function selectAvatar(avatarId: number): Promise<boolean> {
  const epoch = currentClearEpoch()

  try {
    await window.spiritagent.api({
      method: 'PUT',
      path: `/api/companion/avatar/${avatarId}/select`
    })

    if (currentClearEpoch() !== epoch) {
      return false
    }

    $activeAvatarId.set(avatarId)
    clearAvatarSeeds(avatarId)

    const target = $portraitHistory.get().find(entry => entry.avatarId === avatarId)

    if (target?.portraitUrl) {
      $portraitUrl.set(target.portraitUrl)
      const resolvedAssetUrl = target.assetUrl || portraitPersisted.get().assetUrl || undefined
      persistPortrait({
        assetUrl: resolvedAssetUrl ?? null,
        avatarId
      })
      void patchAvatarSeeds({
        avatarId,
        assetUrl: resolvedAssetUrl,
        avatarDisplayUrl: target.portraitUrl
      })
    }

    void hydrateAvatarSeeds()

    return true
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('portrait', 'selectAvatar failed', error)
    }

    return false
  }
}

export function pushPortraitEntry(entry: PortraitEntry): void {
  const current = $portraitHistory.get()
  const next = [...current, entry]

  if (next.length > MAX_HISTORY) {
    next.shift()
  }

  $portraitHistory.set(next)
  $portraitSelectedIdx.set(next.length - 1)
}

export function selectPortraitEntry(idx: number): void {
  const current = $portraitHistory.get()

  if (idx >= 0 && idx < current.length) {
    $portraitSelectedIdx.set(idx)
  }
}

export function clearPortraitHistory(): void {
  $portraitHistory.set([])
  $portraitSelectedIdx.set(0)
}
