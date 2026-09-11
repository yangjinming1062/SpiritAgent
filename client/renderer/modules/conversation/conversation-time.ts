import { getStrings } from '@/shared/strings'

const FIVE_MINUTES_MS = 5 * 60 * 1000

function parseTimestamp(raw?: number): number | null {
  if (typeof raw !== 'number' || !Number.isFinite(raw) || raw <= 0) {
    return null
  }

  return raw > 1e11 ? Math.floor(raw) : Math.floor(raw * 1000)
}

function shouldShowTimeDivider(currentTimestamp: number, lastAnchorTimestamp: number | null): boolean {
  if (lastAnchorTimestamp == null) {
    return true
  }

  return currentTimestamp - lastAnchorTimestamp >= FIVE_MINUTES_MS
}

export function formatConversationTime(timestamp?: number, nowMs: number = Date.now()): string {
  const parsed = parseTimestamp(timestamp)

  if (parsed == null) {
    return ''
  }

  const time = getStrings().chat.time
  const target = new Date(parsed)
  const now = new Date(nowMs)
  const hours = String(target.getHours()).padStart(2, '0')
  const minutes = String(target.getMinutes()).padStart(2, '0')
  const timeStr = `${hours}:${minutes}`
  const startOfTarget = new Date(target.getFullYear(), target.getMonth(), target.getDate()).getTime()
  const startOfNow = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const diffDays = Math.round((startOfNow - startOfTarget) / (24 * 60 * 60 * 1000))

  if (diffDays <= 0) {
    return timeStr
  }

  if (diffDays === 1) {
    return time.yesterdayAt(timeStr)
  }

  if (diffDays < 7) {
    return time.weekdayAt(time.weekdays[target.getDay()] ?? '', timeStr)
  }

  if (target.getFullYear() === now.getFullYear()) {
    return time.monthDayAt(target.getMonth() + 1, target.getDate(), timeStr)
  }

  return time.fullDateAt(target.getFullYear(), target.getMonth() + 1, target.getDate(), timeStr)
}

export function collectTimeDividerIds(items: readonly { id: string; timestamp?: number }[]): Set<string> {
  const ids = new Set<string>()
  let lastAnchorTimestamp: number | null = null

  for (const item of items) {
    const ts = parseTimestamp(item.timestamp)

    if (ts == null) {
      continue
    }

    if (shouldShowTimeDivider(ts, lastAnchorTimestamp)) {
      ids.add(item.id)
    }

    lastAnchorTimestamp = ts
  }

  return ids
}
