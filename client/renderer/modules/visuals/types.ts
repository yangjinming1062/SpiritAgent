// 影像与动作的对外类型以 shared/protocol.ts 的共享契约为准，两端共用同一份定义。
// 桌面消费面额外的播放指令、冻结来源与呈现快照在本文件显式建模。
import type {
  ActionClipKind,
  ActionDefinition,
  ActionDefinitionRequest,
  ActionListResponse,
  ActionReviewStatus,
  ActionUpdateRequest,
  PlaybackReceiptKind,
  PlaybackStatus,
  VisualComboSummary,
  VisualDetail,
  VisualItem,
  VisualJob,
  VisualJobCommand,
  VisualJobCommandRequest,
  VisualJobListResponse,
  VisualJobState,
  VisualListResponse,
  VisualMediaKind,
  VisualPlayback,
  VisualPresentationUpdate,
  VisualRegenerateRequest
} from '@protocol'

import type { ApiSchemas } from '../../../../shared/api.generated'

export type {
  ActionClipKind,
  ActionDefinition,
  ActionDefinitionRequest,
  ActionListResponse,
  ActionReviewStatus,
  ActionUpdateRequest,
  PlaybackReceiptKind,
  PlaybackStatus,
  VisualComboSummary,
  VisualDetail,
  VisualItem,
  VisualJob,
  VisualJobCommand,
  VisualJobCommandRequest,
  VisualJobListResponse,
  VisualJobState,
  VisualListResponse,
  VisualMediaKind,
  VisualPlayback,
  VisualPresentationUpdate,
  VisualRegenerateRequest
}

export type VisualPlayCommand = ApiSchemas['VisualPlayCommand']
export type VisualActualSnapshot = ApiSchemas['ActualPresentationSnapshot']
export type VisualsPresentation = ApiSchemas['VisualPresentationResponse']
