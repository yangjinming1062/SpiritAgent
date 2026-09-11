import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { definePersistedAtom, registerStorageClearHandler } from '@/shared/lib/storage'
import { $gateway } from '@/shared/store/gateway'

// 3D 伙伴的模型与资产目录。
// 后端接口：/api/companion/model；
// 通过网关推送 model.ready 事件，并在 lifecycle=ready 时由 ``hydrateModel`` 拉取一次。

export interface ModelInfo {
  id: number | null
  asset_url: string | null
  species: string | null
  provider: string | null
  has_rig: boolean
  status: string
  rig_type: string
  rig_naming: string
  style: string
  content_hash: string | null
}

interface Companion3DModelResponse {
  id: number
  asset_url: string | null
  provider: string
  species: string
  status: string
  rig_type: string
  rig_naming: string
  has_rig: boolean
  content_hash?: string | null
  clip_map?: Readonly<Record<string, string>>
}

const DEFAULT_MODEL_INFO: ModelInfo = {
  asset_url: null,
  content_hash: null,
  has_rig: false,
  id: null,
  provider: null,
  rig_naming: 'tripo',
  rig_type: 'biped',
  species: null,
  status: 'pending',
  style: 'realistic'
}

function isPersistableModel(val: unknown): val is ModelInfo {
  if (typeof val !== 'object' || val === null) {
    return false
  }

  const v = val as Partial<ModelInfo>

  return v.status === 'succeeded' && typeof v.asset_url === 'string' && Boolean(v.asset_url)
}

const modelInfoPersisted = definePersistedAtom<ModelInfo>({
  fallback: DEFAULT_MODEL_INFO,
  isPersistable: isPersistableModel,
  key: 'da.companion.model'
})

export const $modelInfo = modelInfoPersisted.$atom

// 首次 loadCharacter() 完成（GLB 解析成功或回退到程序化模型）后置为 true。
// 用于门控渲染功率调度器：在它变 true 之前，孵化阶段全速运行，
// 不受 idle/sleep 信号影响。
export const $modelLoadSettled = atom<boolean>(false)
// 引擎解析失败回退到程序化蛋（createProcedural）时置为 true；root.tsx 的渲染级联会
// 在 2D 资产已就绪时改走 2D 渲染层降级，两级皆不可用才停在程序化蛋（DESIGN §1.2）。
export const $glbLoadFailed = atom<boolean>(false)
export const $availableClipNames = atom<Set<string>>(new Set())
// 供应商声明的「语义键 → GLB 内 clip 名」；空表即该产物不含动画。
export const $clipMap = atom<Readonly<Record<string, string>>>({})

// ── Generation progress tracking ──
// model.gen.progress → $modelGenState='generating' + $modelGenProgress
// model.ready        → $modelGenState='succeeded'
// model.failed       → $modelGenState='failed' + $modelGenError
// model.failed 且带 retry_download → 付费结果仍留在后端，只是下载失败；
// $modelRetryable 门控"重试下载"动作（companion.model.retryDownload —— 绝不重新计费生成）。
type ModelGenState = 'idle' | 'generating' | 'succeeded' | 'failed'
export const $modelGenState = atom<ModelGenState>('idle')
export const $modelGenProgress = atom<{ progress: number; stage: string } | null>(null)
export const $modelGenError = atom<string | null>(null)
export const $modelRetryable = atom<boolean>(false)
export const $modelRetryModelId = atom<number | null>(null)

export function setModelFailed(reason: string, opts: { modelId?: number | null; retryDownload?: boolean } = {}): void {
  $modelGenState.set('failed')
  $modelGenError.set(reason)
  $modelGenProgress.set(null)
  $modelRetryable.set(opts.retryDownload === true)
  $modelRetryModelId.set(opts.modelId ?? null)
}

export function clearModelRetry(): void {
  $modelRetryable.set(false)
  $modelRetryModelId.set(null)
}

/** companion.model.retryDownload —— 重放一次已经付费生成结果的下载
 * （后端仅刷新 URL 签名查询，不会重新提交生成）。进度沿同一条 model.* 事件流回传。 */
export async function retryModelDownload(modelId: number): Promise<void> {
  const gateway = $gateway.get()

  if (!gateway) {
    log.warn('model-store', 'retryModelDownload: gateway not ready')

    return
  }

  try {
    await gateway.request('companion.model.retryDownload', { model_id: modelId })
    $modelGenState.set('generating')
    $modelGenProgress.set({ progress: 88, stage: 'downloading' })
    $modelGenError.set(null)
    clearModelRetry()
  } catch (err) {
    log.warn('model-store', 'retryModelDownload failed', err)
  }
}

export function setModelInfo(next: Partial<ModelInfo>): void {
  // 内存更新不受 auth 守卫：events.ts 的 model.ready 是后端 WS 推送，可能在登出
  // 广播到达后 1–2 帧才到；丢掉它意味着用户重登后看到旧 model。持久化侧由
  // definePersistedAtom 的 isPersistable 校验守门——非 succeeded 不会落盘。
  if (next.status !== undefined && next.status !== 'succeeded') {
    // 状态翻成非 succeeded（'failed' / 'generating' 等）：显式清空 asset_url/content_hash，
    // 否则 Partial 合并会让上一帧的 succeeded 字节残留在 atom 里，3D 渲染器会拿旧 asset_url
    // 继续加载、与当前 generation 状态机内部不一致。
    modelInfoPersisted.set({ asset_url: null, content_hash: null, ...next })
  } else {
    modelInfoPersisted.set(next)
  }
}

function resetModel(): void {
  modelInfoPersisted.reset()
  $modelLoadSettled.set(false)
  $glbLoadFailed.set(false)
  $availableClipNames.set(new Set())
  $clipMap.set({})
  $modelGenState.set('idle')
  $modelGenProgress.set(null)
  $modelGenError.set(null)
  $modelRetryable.set(false)
  $modelRetryModelId.set(null)
}

registerStorageClearHandler(resetModel)

export async function hydrateModel(): Promise<void> {
  const result = await authedApi<Companion3DModelResponse | null>({
    path: '/api/companion/model'
  })

  if (!result.ok) {
    if (result.reason === 'err' && !isClientErrorIpc(result.error)) {
      log.warn('model-store', 'hydrateModel failed', result.error)
    }

    // hydrateModel 只读不写：3D 生成由 confirm-front 成功后显式触发，避免 onboarding 中段误启动
    return
  }

  const res = result.value

  if (res && res.asset_url && res.status === 'succeeded') {
    setModelInfo({
      asset_url: res.asset_url,
      content_hash: res.content_hash ?? null,
      has_rig: res.has_rig,
      id: res.id,
      provider: res.provider,
      rig_naming: res.rig_naming ?? 'tripo',
      rig_type: res.rig_type ?? 'biped',
      species: res.species,
      status: res.status
    })
    $clipMap.set(res.clip_map ?? {})
  }
}
