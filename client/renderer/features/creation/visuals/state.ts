import { atom } from 'nanostores'

import type { VisualReceiptBody, VisualsListQuery } from './api'
import type { VisualDetail, VisualItem, VisualJob, VisualPlayCommand, VisualsPresentation } from './types'

export const $visualsItems = atom<VisualItem[]>([])
export const $visualsJobs = atom<VisualJob[]>([])
export const $visualsTotal = atom<number | null>(null)
export const $visualsNextCursor = atom<string | null>(null)
export const $visualsLoading = atom(false)
export const $visualsError = atom<string | null>(null)
export const $visualsDetail = atom<{ id: string; detail: VisualDetail } | null>(null)
export const $visualsDetailError = atom<string | null>(null)
export const $visualsPresentation = atom<VisualsPresentation | null>(null)
export const $visualsPresentationError = atom<string | null>(null)
/** 桌面播放指令；`queued` 只表示受理，实际回执才更新呈现事实。 */
export const $visualPlayback = atom<VisualPlayCommand | null>(null)

/** 列表与详情读取的账户代次、在途请求与续排队列。 */
export const visualsState = {
  detailRequest: null as { id: string; request: Promise<VisualDetail> } | null,
  epoch: 0,
  presentationRequest: 0,
  refreshQuery: {} as VisualsListQuery,
  refreshQueued: false,
  refreshRequest: null as Promise<void> | null
}

/** 演出认领与回执账本：同一 play_id 只由一个可见舞台认领并结算。 */
export const playClients = new Map<string, string>()
export const receiptQueues = new Map<string, Promise<void>>()
export const receiptKinds = new Map<string, VisualReceiptBody['receipt_kind']>()
