import type {
  ActionClipKind,
  ActionDefinition,
  ActionDefinitionRequest,
  ActionLibraryGroup,
  ActionListResponse,
  ActionMediaEntry,
  ActionMediaListResponse,
  ActionReviewStatus,
  ActionUpdateRequest,
  PlaybackReceiptKind,
  PlaybackStatus,
  VisualDetail,
  VisualItem,
  VisualJob,
  VisualJobCommand,
  VisualJobCommandRequest,
  VisualJobListResponse,
  VisualJobState,
  VisualListResponse,
  VisualMediaKind,
  VisualPresentationUpdate,
  VisualRegenerateRequest
} from '@protocol'

import type { ApiSchemas } from '../../../../shared/api.generated'

export type {
  ActionClipKind,
  ActionDefinition,
  ActionDefinitionRequest,
  ActionLibraryGroup,
  ActionListResponse,
  ActionMediaEntry,
  ActionMediaListResponse,
  ActionReviewStatus,
  ActionUpdateRequest,
  PlaybackReceiptKind,
  PlaybackStatus,
  VisualDetail,
  VisualItem,
  VisualJob,
  VisualJobCommand,
  VisualJobCommandRequest,
  VisualJobListResponse,
  VisualJobState,
  VisualListResponse,
  VisualMediaKind,
  VisualPresentationUpdate,
  VisualRegenerateRequest
}

export type VisualPlayCommand = ApiSchemas['VisualPlayCommand']
export type VisualActualSnapshot = ApiSchemas['ActualPresentationSnapshot']
export type VisualsPresentation = ApiSchemas['VisualPresentationResponse']
