import {
  type DesktopBackground,
  type DesktopBackgroundRequest,
  type DesktopMediaReference,
  normalizeUiTheme
} from '@ipc/contracts'

function mediaReference(raw: unknown): DesktopMediaReference | null {
  if (raw === null) {
    return null
  }

  const value = raw as Partial<DesktopMediaReference> | undefined

  if (
    !value ||
    typeof value.url !== 'string' ||
    value.url.length > 8192 ||
    !value.url.startsWith('/api/companion/asset/') ||
    (value.contentHash !== undefined && !/^[a-f0-9]{64}$/i.test(value.contentHash))
  ) {
    throw new Error('无效桌面媒体资源。')
  }

  const pathname = decodeURIComponent(new URL(value.url, 'http://127.0.0.1').pathname)

  if (!pathname.startsWith('/api/companion/asset/') || pathname.split('/').includes('..') || pathname.includes('\0')) {
    throw new Error('无效桌面媒体路径。')
  }

  return { url: value.url, ...(value.contentHash ? { contentHash: value.contentHash } : {}) }
}

export function parseDesktopBackgroundRequest(raw: unknown): DesktopBackgroundRequest {
  const value = raw as Partial<DesktopBackgroundRequest> | null
  const validId = (id: unknown): boolean => id === null || (Number.isSafeInteger(id) && Number(id) > 0)

  if (
    !value ||
    typeof value.authSessionId !== 'string' ||
    (value.playId !== null && (typeof value.playId !== 'string' || !value.playId || value.playId.length > 128)) ||
    !validId(value.setId) ||
    !validId(value.actionId) ||
    (value.actionVersion !== null && (!Number.isSafeInteger(value.actionVersion) || Number(value.actionVersion) < 0)) ||
    !Number.isSafeInteger(value.setEpoch) ||
    Number(value.setEpoch) < 0 ||
    (value.expiresAt !== null &&
      (typeof value.expiresAt !== 'string' || !Number.isFinite(Date.parse(value.expiresAt)))) ||
    (value.kind !== 'loop' && value.kind !== 'once') ||
    (value.continuity !== 'base' && value.continuity !== 'independent') ||
    !validId(value.baseVersion) ||
    (value.continuity === 'base' && value.baseVersion === null) ||
    (value.continuity === 'independent' && value.baseVersion !== null) ||
    typeof value.theme !== 'string' ||
    typeof value.reduceMotion !== 'boolean' ||
    typeof value.paused !== 'boolean' ||
    typeof value.clear !== 'boolean'
  ) {
    throw new Error('无效桌面背景。')
  }

  return {
    authSessionId: value.authSessionId,
    video: mediaReference(value.video),
    poster: mediaReference(value.poster),
    playId: value.playId,
    setId: value.setId as number | null,
    actionId: value.actionId as number | null,
    actionVersion: value.actionVersion as number | null,
    setEpoch: value.setEpoch!,
    expiresAt: value.expiresAt,
    kind: value.kind,
    continuity: value.continuity,
    baseVersion: value.baseVersion as number | null,
    theme: normalizeUiTheme(value.theme),
    reduceMotion: value.reduceMotion,
    paused: value.paused,
    clear: value.clear
  }
}

export function emptyDesktopBackground(accountEpoch: number, revision: number, theme = 'day-clear'): DesktopBackground {
  return {
    accountEpoch,
    revision,
    video: null,
    poster: null,
    playId: null,
    setId: null,
    actionId: null,
    actionVersion: null,
    setEpoch: 0,
    expiresAt: null,
    kind: 'loop',
    continuity: 'independent',
    baseVersion: null,
    theme,
    reduceMotion: false,
    paused: true,
    clear: true
  }
}
