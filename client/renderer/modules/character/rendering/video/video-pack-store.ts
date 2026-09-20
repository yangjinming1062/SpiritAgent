/** 视频动作包 store：水合当前激活包、解析展示 URL、维护加载状态。
 * 包字节经主进程受控资产桥读取（内容哈希缓存）；登出清空，迟到水合丢弃。 */

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import type { VideoPackManifest } from './types'

export interface ActiveVideoPack {
  packId: number
  packVersion: number
  manifest: VideoPackManifest
  /** action → 可用于 <video src> 的本地展示 URL（已预解析 idle；其余懒解析） */
  clipUrls: Map<string, string>
}

export type VideoPackStatus = 'idle' | 'loading' | 'ready' | 'unavailable'

interface VideoPackWire {
  id: number
  pack_version: number
  status: string
  active: boolean
  manifest_url: string | null
}

export const $videoPack = atom<ActiveVideoPack | null>(null)
export const $videoPackStatus = atom<VideoPackStatus>('idle')

let inflight: Promise<void> | null = null

registerStorageClearHandler(() => {
  inflight = null
  $videoPack.set(null)
  $videoPackStatus.set('idle')
})

async function resolveClipUrl(rawUrl: string): Promise<string | null> {
  try {
    return await window.spiritagent.apiAsset({ url: rawUrl, preferCache: true })
  } catch (err) {
    log.warn('video-pack-store', 'clip url resolve failed', err)

    return null
  }
}

/** 水合激活视频包：无激活包 → unavailable；就绪包解析 manifest 并预取 idle 片段。 */
export async function hydrateVideoPack(): Promise<void> {
  if ($auth.get().kind !== 'authenticated') {
    return
  }

  if (inflight) {
    return inflight
  }

  const epoch = currentClearEpoch()

  const load = (async (): Promise<void> => {
    $videoPackStatus.set('loading')

    try {
      const res = await authedApi<{ packs?: VideoPackWire[] }>({ path: '/api/companion/video-packs' })

      if (epoch !== currentClearEpoch()) {
        return
      }

      if (!res.ok || !res.value) {
        $videoPackStatus.set('unavailable')

        return
      }

      const active = (res.value.packs ?? []).find(p => p.active && p.status === 'ready' && p.manifest_url)

      if (!active?.manifest_url) {
        $videoPack.set(null)
        $videoPackStatus.set('unavailable')

        return
      }

      const manifestUrl = await resolveClipUrl(active.manifest_url)

      if (epoch !== currentClearEpoch()) {
        return
      }

      if (!manifestUrl) {
        $videoPackStatus.set('unavailable')

        return
      }

      // eslint-disable-next-line no-restricted-syntax -- manifestUrl 是 apiAsset 桥返回的本地资产 URL（spiritagent-media:// 或 blob），非后端相对路径
      const resp = await fetch(manifestUrl)
      const manifest = (await resp.json()) as VideoPackManifest

      if (epoch !== currentClearEpoch()) {
        return
      }

      if (manifest.schema_version !== 'spiritagent.video.pack/1' || !Array.isArray(manifest.clips)) {
        log.warn('video-pack-store', 'unsupported pack manifest schema')
        $videoPackStatus.set('unavailable')

        return
      }

      const idle = manifest.clips.find(c => c.action === manifest.default_action) ?? manifest.clips[0]
      const idleUrl = idle ? await resolveClipUrl(idle.path) : null

      if (epoch !== currentClearEpoch()) {
        return
      }

      const clipUrls = new Map<string, string>()

      if (idleUrl) {
        clipUrls.set(idle.action, idleUrl)
      }

      $videoPack.set({ packId: active.id, packVersion: active.pack_version, manifest, clipUrls })
      $videoPackStatus.set('ready')
    } catch (err) {
      log.warn('video-pack-store', 'hydrateVideoPack failed', err)

      if (epoch === currentClearEpoch()) {
        $videoPackStatus.set('unavailable')
      }
    } finally {
      if (epoch === currentClearEpoch()) {
        inflight = null
      }
    }
  })()

  inflight = load

  return load
}

/** 按需解析动作片段的展示 URL；失败返回 null（渲染层回退默认动作）。 */
export async function resolveVideoClipUrl(pack: ActiveVideoPack, action: string): Promise<string | null> {
  const cached = pack.clipUrls.get(action)

  if (cached) {
    return cached
  }

  const clip = pack.manifest.clips.find(c => c.action === action)

  if (!clip) {
    return null
  }

  const url = await resolveClipUrl(clip.path)

  if (url) {
    pack.clipUrls.set(action, url)
  }

  return url
}
