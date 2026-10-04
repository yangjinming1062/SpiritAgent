/** 动作模块出口：目录、类型与播放运行时。 */

export {
  $activePlayInstance,
  acceptPlayCommand,
  markPlayInstanceStarted,
  reportReceipt,
  settlePlayInstance,
  shouldStartInstance
} from './action-runtime'
export {
  $actionCatalog,
  $actionCatalogStatus,
  actionCatalogChanged,
  ensurePeekAction,
  hydrateActionCatalog,
  resolveActionClipUrl,
  resolveHitmask
} from './action-store'
export type { ActionCatalogStatus, ActionHitmask, ActiveActionCatalog } from './action-store'
export type {
  ActionClipEntry,
  ActionPackWire,
  ActionPlaybackStatus,
  ActionPlayCommand,
  ActionPlayInstance,
  ActionWire,
  ImageActionClipEntry,
  NormalizedRect,
  PeekGeometry,
  VideoActionClipEntry
} from './action-types'
export { isActionStageVisible, observeActionStageVisibility } from './action-visibility'
