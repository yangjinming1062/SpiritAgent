import type { ApiSchemas } from '../../../../shared/api.generated'

export type DesktopVideoKind = ApiSchemas['DesktopVideoActionResponse']['kind']
export type DesktopVideoContinuity = ApiSchemas['DesktopVideoActionResponse']['continuity']
export type DesktopVideoSource = ApiSchemas['DesktopVideoSetResponse']['source']
export type DesktopVideoTransition = ApiSchemas['DesktopVideoActionResponse']['transition']
export type DesktopVideoAspectPolicy = NonNullable<ApiSchemas['DesktopVideoImageCommand']['aspect_policy']>
export type DesktopVideoRefinementSource = ApiSchemas['DesktopVideoRefineRequest']['source']

export interface DesktopVideoAspectMismatch {
  source_width: number
  source_height: number
  target_width: number
  target_height: number
  media_kind: 'image' | 'video'
  choices: DesktopVideoAspectPolicy[]
  adjust_action_id?: number
  adjust_action_name?: string
  duration_seconds?: number
  adjustment_error?: string
}

export type DesktopVideoRefinementPreview = ApiSchemas['DesktopVideoRefinePreviewResponse']

export type DesktopVideoAsset = ApiSchemas['DesktopVideoCurrentResponse']

export type DesktopVideoDraft = ApiSchemas['DesktopVideoDraftResponse']

export type DesktopVideoTrigger = ApiSchemas['DesktopVideoActionResponse']['allowed_triggers'][number]

export type DesktopVideoAction = ApiSchemas['DesktopVideoActionResponse']

export type DesktopVideoSet = ApiSchemas['DesktopVideoSetResponse']

export type DesktopVideoState = ApiSchemas['DesktopVideoStateResponse']

export type DesktopVideoProposal = ApiSchemas['DesktopVideoProposalResponse']

export type DesktopVideoPlayCommand = ApiSchemas['DesktopVideoPlayCommand']

export type DesktopVideoReceiptStatus = ApiSchemas['DesktopVideoReceipt']['status']
