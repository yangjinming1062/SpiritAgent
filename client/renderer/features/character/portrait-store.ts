import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import {
  currentClearEpoch,
  definePersistedAtom,
  registerStorageClearHandler,
  registerStorageRestoreHandler
} from '@/shared/lib/storage'

import { isRecord } from '../../../../shared/is-record'

import { resolvePortraitUrl } from './avatar-image'
import { clearAvatarSeeds, hydrateAvatarSeeds, patchAvatarSeeds } from './avatar-seeds-store'

interface PersistedPortrait {
  assetUrl: string | null
  avatarId: string | null
}

const DEFAULT_PORTRAIT: PersistedPortrait = {
  assetUrl: null,
  avatarId: null
}

function isPersistablePortrait(val: unknown): val is PersistedPortrait {
  return isRecord(val) && typeof val.assetUrl === 'string' && Boolean(val.assetUrl)
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

// 当前 avatar 行 id ——由 hydrate 与每次创建新行的重生写入。服务端读取当前 avatar 行，这里只是为画廊选择做镜像。
export const $activeAvatarId = atom<string | null>(initialPersisted.avatarId)

// 头像首次生成、重生与微调共用的本次描述；不持久化，成功加载预览后清空。
export const $regenFeedback = atom<string>('')

export interface PortraitEntry {
  assetUrl?: string | null
  avatarId: string | null
  portraitUrl: string | null
}

const MAX_HISTORY = 5

export const $portraitHistory = atom<PortraitEntry[]>([])
export const $portraitSelectedIdx = atom<number>(0)

let portraitRead = 0
let historyRead = 0

/** 并发远端查询只交付当前账户最近一次读取。 */
function beginPortraitRead(): () => boolean {
  const read = ++portraitRead
  const epoch = currentClearEpoch()

  return () => read === portraitRead && epoch === currentClearEpoch()
}

registerStorageClearHandler(() => {
  $portraitUrl.set(null)
  $activeAvatarId.set(null)
  $portraitHistory.set([])
  $portraitSelectedIdx.set(0)
  $regenFeedback.set('')
})

// 设置当前形象；id 变化时作废上一形象的种子缓存，避免自备图参考图串号。
function setActiveAvatar(id: string | null): void {
  portraitRead++
  const previousId = $activeAvatarId.get()

  $activeAvatarId.set(id)

  if (previousId !== id) {
    clearAvatarSeeds(id)
  }
}

function persistPortrait(next: PersistedPortrait): void {
  setActiveAvatar(next.avatarId)
  portraitPersisted.reset()
  portraitPersisted.set({ assetUrl: next.assetUrl, avatarId: next.avatarId })
}

// 头像落地：展示 URL、持久化身份与种子缓存一并更新；assetUrl 为空时种子缓存保持原路径。
function commitPortrait(url: string, assetUrl: string | null | undefined, avatarId: string | null): void {
  $portraitUrl.set(url)
  persistPortrait({ assetUrl: assetUrl ?? null, avatarId })
  void patchAvatarSeeds({ avatarId, assetUrl: assetUrl ?? undefined, avatarDisplayUrl: url })
}

async function restorePortraitFromDisk(assetUrl: string, current: () => boolean): Promise<void> {
  const restored = await resolvePortraitUrl(assetUrl, { cacheOnly: true })

  if (!restored || !current() || $portraitUrl.get()) {
    return
  }

  $portraitUrl.set(restored)
}

function restoreCachedPortrait(): void {
  const saved = portraitPersisted.get()
  $activeAvatarId.set(saved.avatarId)

  if (saved.assetUrl) {
    const epoch = currentClearEpoch()
    void restorePortraitFromDisk(
      saved.assetUrl,
      () => epoch === currentClearEpoch() && $activeAvatarId.get() === saved.avatarId
    )
  }
}

registerStorageRestoreHandler(restoreCachedPortrait)
restoreCachedPortrait()

interface PortraitUrls {
  assetUrl?: string | null
  id?: string | null
}

export async function applyPortrait(
  urls: PortraitUrls,
  isCurrent: () => boolean = () => true
): Promise<{ avatar: string | null }> {
  const epoch = currentClearEpoch()

  if (!isCurrent()) {
    return { avatar: null }
  }

  const avatar = urls.assetUrl === undefined ? null : await resolvePortraitUrl(urls.assetUrl)

  if (currentClearEpoch() !== epoch || !isCurrent()) {
    return { avatar: null }
  }

  if (avatar) {
    commitPortrait(avatar, urls.assetUrl, urls.id ?? $activeAvatarId.get())
  } else if (urls.id != null) {
    // 头像 URL 解析失败但 id 已切换：种子缓存也要随形象作废。
    setActiveAvatar(urls.id)
  }

  return { avatar }
}

export async function hydratePortrait(): Promise<void> {
  const current = beginPortraitRead()

  try {
    const res = await requestResource('companion.get_avatar')

    if (!current()) {
      return
    }

    if (!res.asset_url) {
      return
    }

    const currentCached = portraitPersisted.get()

    const sameIdentity =
      currentCached.avatarId === res.id &&
      portraitAssetIdentity(currentCached.assetUrl) === portraitAssetIdentity(res.asset_url)

    if (sameIdentity) {
      if (!$portraitUrl.get() && currentCached.assetUrl) {
        await restorePortraitFromDisk(currentCached.assetUrl, current)
      }

      // 缓存身份与服务端一致但本地 active id 漂移：纠正 id 时同步作废旧形象种子。
      if (current()) {
        setActiveAvatar(res.id)
      }

      return
    }

    const newAvatar = await resolvePortraitUrl(res.asset_url)

    if (!current()) {
      return
    }

    if (newAvatar) {
      commitPortrait(newAvatar, res.asset_url, res.id)
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
  const read = ++historyRead
  const current = () => read === historyRead && epoch === currentClearEpoch()

  try {
    const res = await requestResource('companion.get_avatar_history')

    if (!current()) {
      return
    }

    const items = res.history.toReversed()
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

    if (!current()) {
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

export async function selectAvatar(avatarId: string): Promise<boolean> {
  const epoch = currentClearEpoch()

  try {
    await requestResource('companion.put_avatar_select', { path: { avatar_id: avatarId } })

    if (currentClearEpoch() !== epoch) {
      return false
    }

    portraitRead++
    $activeAvatarId.set(avatarId)
    clearAvatarSeeds(avatarId)

    const target = $portraitHistory.get().find(entry => entry.avatarId === avatarId)

    if (target?.portraitUrl) {
      commitPortrait(target.portraitUrl, target.assetUrl || portraitPersisted.get().assetUrl || undefined, avatarId)
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
  historyRead++
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
  historyRead++
  $portraitHistory.set([])
  $portraitSelectedIdx.set(0)
}
