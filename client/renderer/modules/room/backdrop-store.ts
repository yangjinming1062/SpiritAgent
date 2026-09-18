// 房间背景状态机：none → pending → ready；换装 invalidated → pending → ready；
// 失败 failed → 重试入口；自备图 waiting_upload（等待用户回传图像）→ adopt → ready / discard → none。

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

export type BackdropStatus = 'failed' | 'none' | 'pending' | 'ready' | 'waiting_upload'

export type RoomPolicy = 'llm_may_replace' | 'locked'

// 自备图行标记：提示词已下发、等待用户上传成品（后端不启动生成任务）
const USER_UPLOAD_SOURCE = 'user_upload'

export interface ActiveBackdrop {
  brief: string
  id: string
  origin?: string
  outfitFingerprint?: string
  prompt?: string
  source?: string
  status: Exclude<BackdropStatus, 'none'>
  url: string
}

export interface RoomHistoryEntry {
  brief: string
  id: string
  origin?: string
  outfitFingerprint?: string
  prompt?: string
  // 列表缩略图；后端未给 thumbnail_url 时与 url 同源。
  thumbnailUrl: string
  // 原图 URL，灯箱查看使用。
  url: string
}

interface RoomBackdropWire {
  id: number | string
  status: 'pending' | 'ready' | 'failed' | 'superseded'
  origin?: string
  source?: string
  brief?: string
  prompt?: string
  url?: string
  thumbnail_url?: string
  outfit_fingerprint?: string
}

interface RoomStateWire {
  active: RoomBackdropWire | null
  history?: RoomBackdropWire[]
  pending: RoomBackdropWire | null
  policy?: string
}

export interface RoomGenerationInput {
  notes?: string
  image?: string
  content_type?: string
}

async function resolveBackdropUrl(url?: string): Promise<string> {
  if (!url) {
    return ''
  }

  if (url.startsWith('data:') || url.startsWith('http:') || url.startsWith('https:')) {
    return url
  }

  if (typeof window !== 'undefined' && window.spiritagent?.apiAsset) {
    try {
      const resolved = await window.spiritagent.apiAsset({ url })

      return resolved || url
    } catch {
      return url
    }
  }

  return url
}

async function toActiveBackdrop(w: RoomBackdropWire): Promise<null | ActiveBackdrop> {
  if (w.status !== 'ready' || !w.url) {
    return null
  }

  const url = await resolveBackdropUrl(w.url)

  return {
    brief: w.brief ?? '',
    id: String(w.id),
    origin: w.origin,
    outfitFingerprint: w.outfit_fingerprint,
    prompt: w.prompt,
    source: w.source,
    status: 'ready',
    url
  }
}

async function toHistoryEntry(w: RoomBackdropWire): Promise<RoomHistoryEntry | null> {
  if (w.status !== 'ready') {
    return null
  }

  const url = await resolveBackdropUrl(w.url)

  if (!url) {
    return null
  }

  const thumbnailUrl = w.thumbnail_url ? await resolveBackdropUrl(w.thumbnail_url) : url

  return {
    brief: w.brief ?? '',
    id: String(w.id),
    origin: w.origin,
    outfitFingerprint: w.outfit_fingerprint,
    prompt: w.prompt,
    thumbnailUrl,
    url
  }
}

function deriveStatus(state: RoomStateWire): BackdropStatus {
  if (state.pending && state.pending.status === 'pending') {
    // 自备图行不跑生成任务：展示为等待上传，而不是无限轮询的生成中
    return state.pending.source === USER_UPLOAD_SOURCE ? 'waiting_upload' : 'pending'
  }

  if (state.active && state.active.status === 'ready') {
    return 'ready'
  }

  if (state.active && state.active.status === 'failed') {
    return 'failed'
  }

  return 'none'
}

let pendingPollTimer: ReturnType<typeof setInterval> | null = null
let pendingPollCount = 0
let generationVersion = 0
let submittingGeneration = false
const MAX_PENDING_POLL_COUNT = 20

function stopPendingPoll(): void {
  if (pendingPollTimer !== null) {
    clearInterval(pendingPollTimer)
    pendingPollTimer = null
    pendingPollCount = 0
  }
}

function startPendingPoll(): void {
  if (pendingPollTimer !== null || submittingGeneration) {
    return
  }

  pendingPollCount = 0
  pendingPollTimer = setInterval(() => {
    pendingPollCount++

    if (pendingPollCount > MAX_PENDING_POLL_COUNT) {
      stopPendingPoll()

      if ($backdropStatus.get() === 'pending') {
        $backdropStatus.set('failed')
        notify({ kind: 'warning', message: getStrings().living.toasts.roomSlow })
      }

      return
    }

    const version = generationVersion
    void authedApi<RoomStateWire>({ path: '/api/companion/room' }).then(result => {
      if (version !== generationVersion || submittingGeneration) {
        return
      }

      if (!result.ok) {
        if (result.reason === 'unauth') {
          stopPendingPoll()
        }

        return
      }

      if (!result.value) {
        return
      }

      const val = result.value

      if (!val.pending || val.pending.status !== 'pending') {
        stopPendingPoll()
        void applyRoomState(val)

        if (val.active && val.active.status === 'ready') {
          notify({ kind: 'success', message: getStrings().living.toasts.roomReady })
        }
      }
    })
  }, 3500)
}

function normalizePolicy(raw: unknown): RoomPolicy {
  return raw === 'locked' ? 'locked' : 'llm_may_replace'
}

export const $backdropStatus = atom<BackdropStatus>('none')
export const $activeBackdrop = atom<null | ActiveBackdrop>(null)
export const $pendingBackdrop = atom<null | ActiveBackdrop>(null)
export const $roomHistory = atom<RoomHistoryEntry[]>([])
export const $roomPolicy = atom<RoomPolicy>('llm_may_replace')

function resetRoomBackdrop(): void {
  generationVersion++
  submittingGeneration = false
  stopPendingPoll()
  $backdropStatus.set('none')
  $activeBackdrop.set(null)
  $pendingBackdrop.set(null)
  $roomHistory.set([])
  $roomPolicy.set('llm_may_replace')
}

registerStorageClearHandler(resetRoomBackdrop)

async function applyRoomState(state: Partial<RoomStateWire> & Pick<RoomStateWire, 'active'>): Promise<void> {
  const nextStatus = deriveStatus(state as RoomStateWire)
  $backdropStatus.set(nextStatus)

  // waiting_upload 没有服务端任务在跑：不轮询也不误报超时
  if (nextStatus === 'pending') {
    startPendingPoll()
  } else {
    stopPendingPoll()
  }

  // policy / history 只在水合（GET）或用户主动操作后才回写；WS 单事件没带就不覆盖，
  // 否则会静默清掉用户已经设置过的回滚历史或锁定政策。
  if (state.policy !== undefined) {
    $roomPolicy.set(normalizePolicy(state.policy))
  }

  if (state.active) {
    const active = await toActiveBackdrop(state.active)
    $activeBackdrop.set(active)
  } else {
    $activeBackdrop.set(null)
  }

  if (state.pending) {
    const pending = await toActiveBackdrop(state.pending)
    $pendingBackdrop.set(
      pending ?? {
        brief: state.pending.brief ?? '',
        id: String(state.pending.id),
        origin: state.pending.origin,
        outfitFingerprint: state.pending.outfit_fingerprint,
        prompt: state.pending.prompt,
        source: state.pending.source,
        status: 'pending',
        url: ''
      }
    )
  } else {
    $pendingBackdrop.set(null)
  }

  if (Array.isArray(state.history)) {
    const history: RoomHistoryEntry[] = []

    for (const entry of state.history) {
      const item = await toHistoryEntry(entry)

      if (item) {
        history.push(item)
      }
    }

    $roomHistory.set(history.slice(0, 5))
  }
}

// 冷启动水合：拉一次完整房间态。失败保留 none 状态（玻璃底 + 占位）。
export function hydrateRoomBackdrop(): void {
  if (submittingGeneration) {
    return
  }

  const version = generationVersion
  void authedApi<RoomStateWire>({ path: '/api/companion/room' }).then(result => {
    if (version !== generationVersion || submittingGeneration) {
      return
    }

    if (!result.ok) {
      if (result.reason === 'err') {
        log.warn('room', 'hydrate failed:', result.error)
      }

      return
    }

    if (result.value) {
      void applyRoomState(result.value)
    }
  })
}

export async function regenerateRoom(input: RoomGenerationInput = {}): Promise<boolean> {
  const prevStatus = $backdropStatus.get()

  if (prevStatus === 'pending' || submittingGeneration) {
    return false
  }

  const epoch = currentClearEpoch()
  generationVersion++
  submittingGeneration = true
  $backdropStatus.set('pending')

  const result = await authedApi({
    body: { intent: 'rebuild', ...input },
    method: 'POST',
    path: '/api/companion/room/generate'
  })

  if (currentClearEpoch() !== epoch) {
    return false
  }

  submittingGeneration = false

  if (!result.ok) {
    stopPendingPoll()

    if ($backdropStatus.get() === 'pending') {
      $backdropStatus.set(prevStatus)
    }

    if (result.reason === 'err') {
      notify({ kind: 'warning', message: getStrings().living.toasts.roomRegenerateFailed })
    }

    hydrateRoomBackdrop()

    return false
  }

  if ($backdropStatus.get() === 'pending') {
    startPendingPoll()
  } else if ($backdropStatus.get() === 'ready') {
    hydrateRoomBackdrop()
  }

  return true
}

// 主进程错误含状态码、路径与 JSON 错误体，取 detail 里的公开文案；解析不了就用兜底。
const roomErrMsg = (err: unknown): string => backendDetailMessage(err, getStrings().living.toasts.roomRegenerateFailed)

// 自备图第一步：下发提示词并在后端创建等待上传的 pending 行（不启动生图任务）。
// 用户取消或离开后行仍在，房间页展示等待上传态；放弃走 discardPendingRoom。
export async function prepareRoomPrompt(notes?: string): Promise<string> {
  const result = await authedApi<{ prompt: string }>({
    body: { intent: 'rebuild', notes: notes || undefined },
    method: 'POST',
    path: '/api/companion/room/prompt'
  })

  if (!result.ok) {
    throw new Error(result.reason === 'err' ? roomErrMsg(result.error) : '')
  }

  if (!result.value) {
    throw new Error(getStrings().living.toasts.roomRegenerateFailed)
  }

  void hydrateRoomBackdrop()

  return result.value.prompt
}

// 自备图采纳：把用户上传的房间图挂到等待上传的行上，服务端按生成链同一语义转 ready。
export async function adoptRoomImage(image: { base64: string; contentType: string }): Promise<void> {
  const pending = $pendingBackdrop.get()

  // 水合未完成或失败时没有可挂靠的行：必须显式失败，静默返回会让调用方误报「房间已就绪」。
  if (!pending) {
    throw new Error(getStrings().living.toasts.roomRegenerateFailed)
  }

  const id = Number.parseInt(pending.id, 10)

  if (Number.isNaN(id)) {
    throw new Error(getStrings().living.toasts.roomRegenerateFailed)
  }

  const result = await authedApi({
    body: { image: image.base64, content_type: image.contentType },
    method: 'POST',
    path: `/api/companion/room/${id}/adopt`
  })

  if (!result.ok) {
    // unauth 等非 err 失败也要带文案：房间页直传路径直接取 message 弹 toast，空字符串会变成空白气泡。
    throw new Error(
      result.reason === 'err' ? roomErrMsg(result.error) : getStrings().living.toasts.roomRegenerateFailed
    )
  }

  void hydrateRoomBackdrop()
}

// 放弃等待上传的自备图行。
export async function discardPendingRoom(): Promise<void> {
  const pending = $pendingBackdrop.get()

  if (!pending) {
    return
  }

  const id = Number.parseInt(pending.id, 10)

  if (Number.isNaN(id)) {
    return
  }

  const result = await authedApi({ method: 'POST', path: `/api/companion/room/${id}/discard` })

  if (!result.ok) {
    throw new Error(
      result.reason === 'err' ? roomErrMsg(result.error) : getStrings().living.toasts.roomRegenerateFailed
    )
  }

  void hydrateRoomBackdrop()
}

export async function rollbackRoom(backdropId: string): Promise<void> {
  const id = Number.parseInt(backdropId, 10)

  if (Number.isNaN(id)) {
    return
  }

  const result = await authedApi({
    body: { backdrop_id: id },
    method: 'POST',
    path: '/api/companion/room/activate'
  })

  if (!result.ok) {
    if (result.reason === 'err') {
      notify({ kind: 'warning', message: getStrings().living.toasts.roomRollbackFailed })
    }

    return
  }

  notify({ kind: 'success', message: getStrings().living.toasts.roomRollbackSuccess })
  void hydrateRoomBackdrop()
}

/** 删除历史房间图（非当前 active）；成功后重水合。 */
export async function deleteRoomHistory(backdropId: string): Promise<void> {
  const id = Number.parseInt(backdropId, 10)
  const fallback = getStrings().living.toasts.roomDeleteFailed

  if (Number.isNaN(id)) {
    throw new Error(fallback)
  }

  const result = await authedApi({
    method: 'DELETE',
    path: `/api/companion/room/${id}`
  })

  if (!result.ok) {
    throw new Error(result.reason === 'err' ? backendDetailMessage(result.error, fallback) : fallback)
  }

  void hydrateRoomBackdrop()
}

export async function setRoomPolicy(policy: RoomPolicy): Promise<void> {
  const result = await authedApi({
    body: { policy },
    method: 'PATCH',
    path: '/api/companion/room/policy'
  })

  if (!result.ok) {
    if (result.reason === 'err') {
      notify({ kind: 'warning', message: getStrings().living.toasts.roomLockFailed })
    }

    return
  }

  $roomPolicy.set(policy)
}

// WS 事件入口：由 app/runtime/gateway-event-router.ts 调用。
export function onBackdropEvent(event: { payload?: unknown; type: string }): void {
  const p = (event.payload ?? {}) as Partial<RoomBackdropWire> & {
    active_backdrop_id?: number | string
    backdrop_id?: number | string
    reason?: string
    stage?: string
    utterance?: string
  }

  if (event.type === 'companion.room.ready') {
    const backdropId = p.backdrop_id ?? p.id

    if (!backdropId || !p.url) {
      return
    }

    stopPendingPoll()

    // WS payload 不带 history / policy：先立即切 active backdrop，
    // 再触发 hydrateRoomBackdrop() 把 history 与 policy 重新对账。
    void applyRoomState({
      active: {
        brief: p.brief,
        id: String(backdropId),
        origin: p.origin,
        outfit_fingerprint: p.outfit_fingerprint,
        prompt: p.prompt,
        status: 'ready',
        url: p.url
      },
      pending: null
    })

    void hydrateRoomBackdrop()
    notify({ kind: 'success', message: getStrings().living.toasts.roomReadyAlt })

    return
  }

  if (event.type === 'companion.room.invalidated') {
    // 换装或回滚：active 保留到新图 ready；这里只切到 pending 占位。
    $backdropStatus.set('pending')
    startPendingPoll()

    return
  }

  if (event.type === 'companion.room.progress') {
    $backdropStatus.set('pending')
    startPendingPoll()

    return
  }

  if (event.type === 'companion.room.failed') {
    stopPendingPoll()
    $backdropStatus.set('failed')
    notify({
      kind: 'warning',
      message: p.utterance || getStrings().living.toasts.roomFailedFallback
    })
  }
}
