import type { VideoQueueState } from '@protocol'

export type DesktopVideoKind = 'loop' | 'once'
export type DesktopVideoContinuity = 'base' | 'independent'
export type DesktopVideoSource = 'ai' | 'image_upload' | 'video_upload'
export type DesktopVideoTransition = 'base' | 'fade'
export type DesktopVideoAspectPolicy = 'ask' | 'keep' | 'expand'
export type DesktopVideoRefinementSource = 'current' | 'candidate'

export interface DesktopVideoAspectMismatch {
  reason: 'aspect_ratio_mismatch'
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

export interface DesktopVideoRefinementPreview {
  duration_seconds: number
  width: number
  height: number
}

export interface DesktopVideoAsset {
  video_url: string
  poster_url: string | null
  image_url: string | null
  duration_ms: number | null
  width: number | null
  height: number | null
  geometry_mode: 'target' | 'keep'
  continuity: DesktopVideoContinuity
  transition: DesktopVideoTransition
  base_version: number | null
}

export interface DesktopVideoDraft {
  id: string
  stage: string
  source: 'user_requested' | 'autonomous' | 'external_upload'
  image_url: string | null
  image_confirmed: boolean
  feedback: string
  submission_unknown: boolean
  candidate_video_url: string | null
  candidate_poster_url: string | null
  review_reason: string
  base_version: number | null
  target_width: number
  target_height: number
  resolution_revision: number
  geometry_mode: 'target' | 'keep'
  duration_seconds: number
  video_refinement: boolean
  frame_width: number | null
  frame_height: number | null
  error: string | null
  video_queue?: VideoQueueState | null
}

export type DesktopVideoTrigger = 'periodic' | 'user_return' | 'chat' | 'manual'

export interface DesktopVideoAction {
  id: number
  set_id: number
  current: DesktopVideoAsset | null
  draft: DesktopVideoDraft | null
  playable: boolean
  needs_update: boolean
  unavailable_reason: string | null
  transition: DesktopVideoTransition
  key: string
  name: string
  description: string
  kind: DesktopVideoKind
  target_duration_seconds: number
  duration_seconds: number
  status: 'queued' | 'processing' | 'ready' | 'failed' | 'review_pending'
  stage: string
  error: string | null
  video_queue?: VideoQueueState | null
  enabled: boolean
  preset: boolean
  use_when: string[]
  avoid_when: string[]
  allowed_triggers: DesktopVideoTrigger[]
  continuity: DesktopVideoContinuity
  base_version: number | null
  poster_url: string | null
  video_url: string | null
  version: number
  actual_duration_ms: number | null
}

export interface DesktopVideoPromptReference {
  label: string
  url: string
}

export interface DesktopVideoPrompt {
  action_id: number
  description: string
  duration_seconds: number
  kind: DesktopVideoKind
  prompt: string
  references: DesktopVideoPromptReference[]
  draft_id: string | null
  base_version: number
  set_id: number
}

export interface DesktopVideoSet {
  id: number
  title: string
  is_default: boolean
  source: DesktopVideoSource
  outfit_id: number | null
  scene_id: number | null
  base_image_url: string | null
  base_version: number
  target_width: number
  target_height: number
  resolution_revision: number
  status: string
  actions: DesktopVideoAction[]
  proposals: DesktopVideoProposal[]
  created_at: string
  is_current: boolean
}

export interface DesktopVideoState {
  current: DesktopVideoSet | null
  selected_action_id: number | null
  loop_action_id: number | null
  set_epoch: number
  pinned: boolean
  autonomous_enabled: boolean
  version: number
  preparation_error: string | null
}

export interface DesktopVideoProposal {
  proposal_id: number
  set_id: number
  action_id: number | null
  status: string
  review_reason: string
  name: string
  description: string
  created_at: string | null
}

export interface DesktopVideoPlayCommand {
  play_id: string
  transition: DesktopVideoTransition
  set_id: number
  action_id: number
  set_epoch: number
  kind: DesktopVideoKind
  continuity: DesktopVideoContinuity
  base_version: number | null
  expires_at: string
  video_url: string
  poster_url: string | null
  geometry_mode: 'target' | 'keep'
  version: number
}

export type DesktopVideoReceiptStatus = 'started' | 'completed' | 'interrupted' | 'failed' | 'rejected'
