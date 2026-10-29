import {
  DEFAULT_STAGE_LAYOUT,
  type DesktopBackground,
  type DesktopBackgroundRequest,
  type DesktopMediaReference,
  type DesktopStageLayer,
  type DesktopStageLayout,
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

const STAGE_LAYERS: readonly DesktopStageLayer[] = ['scene', 'actor', 'cinematic', 'effects']

function validUnitPair(pair: unknown): pair is [number, number] {
  return (
    Array.isArray(pair) &&
    pair.length === 2 &&
    pair.every(item => typeof item === 'number' && Number.isFinite(item) && item >= 0 && item <= 1)
  )
}

function validStageLayout(layout: Partial<DesktopStageLayout>): layout is DesktopStageLayout {
  const { safeArea, actorPosition, layerOrder } = layout

  return (
    (layout.fit === 'contain' || layout.fit === 'cover') &&
    validUnitPair(actorPosition) &&
    Array.isArray(safeArea) &&
    safeArea.length === 4 &&
    safeArea.every(item => typeof item === 'number' && Number.isFinite(item) && item >= 0 && item <= 1) &&
    safeArea[0] < safeArea[2] &&
    safeArea[1] < safeArea[3] &&
    Array.isArray(layerOrder) &&
    layerOrder.length > 0 &&
    layerOrder.length <= STAGE_LAYERS.length &&
    new Set(layerOrder).size === layerOrder.length &&
    layerOrder.every(item => STAGE_LAYERS.includes(item))
  )
}

export function parseDesktopBackgroundRequest(raw: unknown): DesktopBackgroundRequest {
  const value = raw as Partial<DesktopBackgroundRequest> | null
  const validId = (id: unknown): boolean => id === null || isResourceId(id)
  const validVersion = (v: unknown): boolean => v === null || (Number.isSafeInteger(v) && Number(v) > 0)
  const layout = value?.layout as Partial<DesktopStageLayout> | undefined

  if (
    !value ||
    typeof value.authSessionId !== 'string' ||
    (value.playId !== null && (typeof value.playId !== 'string' || !value.playId || value.playId.length > 128)) ||
    !validId(value.visualId) ||
    !validVersion(value.visualVersion) ||
    !validId(value.actorVisualId) ||
    !validVersion(value.actorVisualVersion) ||
    (value.visualId === null) !== (value.visualVersion === null) ||
    (value.actorVisualId === null) !== (value.actorVisualVersion === null) ||
    (value.playTarget === 'actor' && (value.actor === null || value.actorVisualId === null)) ||
    !Number.isSafeInteger(value.presentationEpoch) ||
    Number(value.presentationEpoch) < 0 ||
    (value.expiresAt !== null &&
      (typeof value.expiresAt !== 'string' || !Number.isFinite(Date.parse(value.expiresAt)))) ||
    (value.kind !== 'loop' && value.kind !== 'once') ||
    (value.actorKind !== 'loop' && value.actorKind !== 'once') ||
    (value.playTarget !== 'background' && value.playTarget !== 'actor') ||
    !(
      value.actorBaseKey === null ||
      (typeof value.actorBaseKey === 'string' && /^\d+:\d+$/.test(value.actorBaseKey))
    ) ||
    !layout ||
    !validStageLayout(layout) ||
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
    (value.backgroundKind !== 'scene' && value.backgroundKind !== 'cinematic') ||
    typeof value.phaseMedia !== 'boolean' ||
    typeof value.clear !== 'boolean'
  ) {
    throw new Error('无效桌面背景。')
  }

  return {
    authSessionId: value.authSessionId,
    video: mediaReference(value.video),
    poster: mediaReference(value.poster),
    outfit: mediaReference(value.outfit),
    actor: mediaReference(value.actor),
    actorBaseKey: value.actorBaseKey,
    layout,
    playTarget: value.playTarget,
    playId: value.playId,
    visualId: value.visualId as string | null,
    visualVersion: value.visualVersion as number | null,
    actorVisualId: value.actorVisualId as string | null,
    actorVisualVersion: value.actorVisualVersion as number | null,
    presentationEpoch: value.presentationEpoch!,
    expiresAt: value.expiresAt,
    kind: value.kind,
    actorKind: value.actorKind,
    continuity: value.continuity,
    transition: value.transition,
    geometryMode: value.geometryMode,
    inputImageHash: value.inputImageHash as string | null,
    theme: normalizeUiTheme(value.theme),
    reduceMotion: value.reduceMotion,
    paused: value.paused,
    backgroundKind: value.backgroundKind,
    phaseMedia: value.phaseMedia,
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
    actor: null,
    actorBaseKey: null,
    layout: DEFAULT_STAGE_LAYOUT,
    playTarget: 'background',
    playId: null,
    visualId: null,
    visualVersion: null,
    actorVisualId: null,
    actorVisualVersion: null,
    presentationEpoch: 0,
    expiresAt: null,
    kind: 'loop',
    actorKind: 'loop',
    continuity: 'independent',
    transition: 'fade',
    geometryMode: 'target',
    inputImageHash: null,
    theme,
    reduceMotion: false,
    paused: true,
    backgroundKind: 'scene',
    phaseMedia: false,
    clear: true
  }
}
