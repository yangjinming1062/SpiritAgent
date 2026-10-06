import { atom } from 'nanostores'

import { triggerHaptic } from '@/shared/lib/haptics'
import { backendDetailMessage, unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { getStrings } from '@/shared/strings'

export type NotificationKind = 'error' | 'warning' | 'info' | 'success'

interface NotificationAction {
  label: string
  onClick: () => void
}

export interface AppNotification {
  id: string
  kind: NotificationKind
  title?: string
  message: string
  detail?: string
  action?: NotificationAction
}

interface NotificationInput {
  kind?: NotificationKind
  title?: string
  message: string
  detail?: string
  action?: NotificationAction
  durationMs?: number
}

let notificationCounter = 0
const timers = new Map<string, ReturnType<typeof setTimeout>>()

export const $notifications = atom<AppNotification[]>([])

function defaultDuration(kind: NotificationKind): number {
  if (kind === 'error' || kind === 'warning') {
    return 0
  }

  return 5_000
}

function cleanErrorText(value: string): string {
  return value.replace(/^Error:\s*/, '').trim()
}

const ERROR_SUMMARIES: { test: (msg: string) => boolean; summarize: (msg: string) => string }[] = [
  {
    test: msg => /incorrect api key provided/i.test(msg) || /['"]code['"]\s*:\s*['"]invalid_api_key['"]/i.test(msg),
    summarize: msg => {
      const status = msg.match(/(?:error code|status(?:Code)?)[^\d]*(\d{3})/i)?.[1]
      const errors = getStrings().notifications.errors

      return status ? errors.openaiRejectedApiKeyWithStatus(status) : errors.openaiRejectedApiKey
    }
  },
  {
    test: msg => /method not allowed/i.test(msg),
    summarize: () => {
      const strings = getStrings()

      return strings.notifications.errors.methodNotAllowed(strings.brand.fullName)
    }
  },
  {
    test: msg => /microphone permission/i.test(msg),
    summarize: () => getStrings().notifications.errors.microphonePermission
  }
]

function summarizeErrorMessage(message: string, fallback: string): string {
  const rule = ERROR_SUMMARIES.find(r => r.test(message))

  if (rule) {
    return rule.summarize(message)
  }

  return message.length > 180 ? fallback : message || fallback
}

function readableError(error: unknown, fallback: string): { message: string; detail?: string } {
  let detail: string

  if (error instanceof Error) {
    // 先 cleanErrorText 再走共享的全文 JSON 解析取 detail 公开文案；解析不了保留清洗后的原文，纯文案错误不退化为兜底。
    const raw = cleanErrorText(unwrapIpcErrorMessage(error))
    detail = backendDetailMessage(raw, raw)
  } else if (typeof error === 'string') {
    detail = error
  } else {
    detail = fallback
  }

  const summary = summarizeErrorMessage(detail, fallback)

  return { message: summary, detail: detail === summary ? undefined : detail }
}

export function notify(input: NotificationInput): void {
  const kind = input.kind ?? 'info'

  // 触感在通知创建时触发一次；info 静默，不随渲染或 dismiss 重复触发。
  if (kind !== 'info') {
    triggerHaptic(kind)
  }

  const id = `${Date.now()}-${notificationCounter++}`

  const notification: AppNotification = {
    id,
    kind,
    title: input.title,
    message: input.message,
    detail: input.detail,
    action: input.action
  }

  $notifications.set([notification, ...$notifications.get()].slice(0, 4))

  const duration = input.durationMs ?? defaultDuration(kind)

  if (duration > 0) {
    timers.set(
      id,
      window.setTimeout(() => dismissNotification(id), duration)
    )
  }
}

export function notifyError(error: unknown, fallback: string): void {
  const readable = readableError(error, fallback)

  notify({
    kind: 'error',
    title: fallback,
    message: readable.message,
    detail: readable.detail
  })
}

export function dismissNotification(id: string): void {
  window.clearTimeout(timers.get(id))
  timers.delete(id)
  $notifications.set($notifications.get().filter(item => item.id !== id))
}

export function clearNotifications(): void {
  for (const timer of timers.values()) {
    window.clearTimeout(timer)
  }

  timers.clear()
  $notifications.set([])
}

registerStorageClearHandler(clearNotifications)
