/** 视频动作包 store：水合当前激活包、解析展示 URL、维护加载状态。
 * 包字节经主进程受控资产桥读取（内容哈希缓存）；登出清空，迟到水合丢弃。 */

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
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

/** 按参考生成的任务阶段（对应后端 companion.video.progress 的 stage） */
export type VideoGenStage = 'script' | 'submit' | 'generate' | 'download' | 'process' | 'publish'

interface VideoPackWire {
  id: number
  pack_version: number
  status: string
  active: boolean
  manifest_url: string | null
}

export const $videoPack = atom<ActiveVideoPack | null>(null)
export const $videoPackStatus = atom<VideoPackStatus>('idle')

// 生成状态机：事件驱动（progress/failed）优先，hydrate 用包列表兜底识别 processing 包；
// 登出清空。failed 携带后端公开文案，可从生成入口重试。
export const $videoGenState = atom<'idle' | 'generating' | 'failed'>('idle')
export const $videoGenStage = atom<VideoGenStage | null>(null)
export const $videoGenError = atom<string | null>(null)

let inflight: Promise<void> | null = null
let generationRevision = 0
let requestingGeneration = false

/** 事件优先于旧请求响应；终态事件要求在已有水合之后重新读取。 */
export function videoPackEventReceived(): void {
  generationRevision += 1
}

registerStorageClearHandler(() => {
  inflight = null
  generationRevision += 1
  requestingGeneration = false
  $videoPack.set(null)
  $videoPackStatus.set('idle')
  $videoGenState.set('idle')
  $videoGenStage.set(null)
  $videoGenError.set(null)
})

async function resolveClipUrl(rawUrl: string): Promise<string | null> {
  try {
    return await window.spiritagent.apiAsset({ url: rawUrl, preferCache: true })
  } catch (err) {
    log.warn('video-pack-store', 'clip url resolve failed', err)

    return null
  }
}

/** 水合激活视频包：无激活包 → unavailable；就绪包解析 manifest 并预取 idle 片段。
 * 同时识别 processing 包还原生成中状态（事件丢失或离线期间的兜底）。 */
export async function hydrateVideoPack(refresh = false): Promise<void> {
  if ($auth.get().kind !== 'authenticated') {
    return
  }

  if (inflight) {
    if (refresh) {
      const epoch = currentClearEpoch()
      await inflight

      if (epoch === currentClearEpoch()) {
        await hydrateVideoPack()
      }

      return
    }

    return inflight
  }

  const epoch = currentClearEpoch()
  const revision = generationRevision

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

      const packs = res.value.packs ?? []
      const processing = packs.find(p => p.status === 'processing')

      if (revision !== generationRevision) {
        // 在途快照不覆盖随后收到的生成事件。
      } else if (processing) {
        $videoGenState.set('generating')
        $videoGenError.set(null)
      } else if ($videoGenState.get() === 'generating') {
        // 服务端已无进行中的任务（如处理进程重启按失败落库），本地生成态收敛；
        // 具体失败文案以 companion.video.failed 事件为准。
        $videoGenState.set('idle')
        $videoGenStage.set(null)
      }

      const active = packs.find(p => p.active && p.status === 'ready' && p.manifest_url)

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

/** 发起按参考生成（LLM 演绎脚本 → i2v → 服务端处理）；进度与结果经 companion.video 事件回流。
 * 请求被拒绝（守卫 / 供应商未配置）时把后端公开文案写入失败态，不进入 generating。 */
export async function generateVideoPack(opts: { force?: boolean } = {}): Promise<boolean> {
  if ($auth.get().kind !== 'authenticated' || requestingGeneration || $videoGenState.get() === 'generating') {
    return false
  }

  const epoch = currentClearEpoch()

  const revision = generationRevision
  requestingGeneration = true

  const res = await authedApi<VideoPackWire>({
    body: { force: opts.force === true },
    method: 'POST',
    path: '/api/companion/video-packs/generate'
  })

  if (epoch !== currentClearEpoch()) {
    return false
  }

  requestingGeneration = false

  if (revision !== generationRevision) {
    return res.ok
  }

  if (!res.ok) {
    if (res.reason === 'err') {
      $videoGenState.set('failed')
      $videoGenError.set(backendDetailMessage(res.error, '视频形象生成请求失败，请稍后重试'))
    }

    return false
  }

  if (!res.value) {
    return false
  }

  $videoGenState.set(res.value.status === 'processing' ? 'generating' : 'idle')
  $videoGenStage.set(res.value.status === 'processing' ? 'script' : null)
  $videoGenError.set(null)

  if (res.value.status !== 'processing') {
    await hydrateVideoPack(true)
  }

  return true
}
