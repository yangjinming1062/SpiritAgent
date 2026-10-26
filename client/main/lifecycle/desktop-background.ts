import {
  type DesktopBackground,
  type DesktopBackgroundRequest,
  type DesktopMediaReference,
  normalizeUiTheme
} from '@ipc/contracts'

import { isResourceId } from '../../../shared/resource-id'

function mediaReference(raw: unknown): DesktopMediaReference | null {
  if (raw === null) {
    return null
  }

  const value = raw as Partial<DesktopMediaReference> | undefined

  if (
    !value ||
    typeof value.url !== 'string' ||
    value.url.length > 8192 ||
    !value.url.startsWith('/api/companion/asset/')
  ) {
    throw new Error('无效桌面媒体资源。')
  }

  const pathname = decodeURIComponent(new URL(value.url, 'http://127.0.0.1').pathname)

  if (!pathname.startsWith('/api/companion/asset/') || pathname.split('/').includes('..') || pathname.includes('\0')) {
    throw new Error('无效桌面媒体路径。')
  }

  return { url: value.url }
}

export function parseDesktopBackgroundRequest(raw: unknown): DesktopBackgroundRequest {
  const value = raw as Partial<DesktopBackgroundRequest> | null
  const validId = (id: unknown): boolean => id === null || isResourceId(id)

  if (
    !value ||
    typeof value.authSessionId !== 'string' ||
    (value.playId !== null && (typeof value.playId !== 'string' || !value.playId || value.playId.length > 128)) ||
    !validId(value.visualId) ||
    (value.visualVersion !== null && (!Number.isSafeInteger(value.visualVersion) || Number(value.visualVersion) < 0)) ||
    !Number.isSafeInteger(value.presentationEpoch) ||
    Number(value.presentationEpoch) < 0 ||
    (value.expiresAt !== null &&
      (typeof value.expiresAt !== 'string' || !Number.isFinite(Date.parse(value.expiresAt)))) ||
    (value.kind !== 'loop' && value.kind !== 'once') ||
    (value.continuity !== 'base' && value.continuity !== 'independent') ||
    (value.transition !== 'natural' && value.transition !== 'fade') ||
    (value.geometryMode !== 'target' && value.geometryMode !== 'keep') ||
    !(
      value.inputImageHash === null ||
      (typeof value.inputImageHash === 'string' && /^[0-9a-f]{64}$/.test(value.inputImageHash))
    ) ||
    (value.continuity === 'base' && value.inputImageHash === null) ||
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
    outfit: mediaReference(value.outfit),
    playId: value.playId,
    visualId: value.visualId as string | null,
    visualVersion: value.visualVersion as number | null,
    presentationEpoch: value.presentationEpoch!,
    expiresAt: value.expiresAt,
    kind: value.kind,
    continuity: value.continuity,
    transition: value.transition,
    geometryMode: value.geometryMode,
    inputImageHash: value.inputImageHash as string | null,
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
    outfit: null,
    playId: null,
    visualId: null,
    visualVersion: null,
    presentationEpoch: 0,
    expiresAt: null,
    kind: 'loop',
    continuity: 'independent',
    transition: 'fade',
    geometryMode: 'target',
    inputImageHash: null,
    theme,
    reduceMotion: false,
    paused: true,
    clear: true
  }
}
