export type DesktopVideoKind = 'loop' | 'once'
export type DesktopVideoContinuity = 'base' | 'independent'
export type DesktopVideoTrigger = 'periodic' | 'user_return' | 'chat' | 'manual'

export interface DesktopVideoAction {
  id: number
  set_id: number
  key: string
  name: string
  description: string
  kind: DesktopVideoKind
  target_duration_seconds: number
  duration_seconds: number
  status: 'queued' | 'processing' | 'ready' | 'failed' | 'review_pending'
  stage: string
  error: string | null
  enabled: boolean
  preset: boolean
  use_when: string[]
  avoid_when: string[]
  allowed_triggers: DesktopVideoTrigger[]
  continuity: DesktopVideoContinuity
  base_version: number | null
  poster_url: string | null
  video_url: string | null
  candidate_video_url?: string | null
  candidate_poster_url?: string | null
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
  set_id: number
}

export interface DesktopVideoSet {
  id: number
  title: string
  outfit_id: number | null
  scene_id: number | null
  context_hash: string
  base_image_url: string | null
  base_version: number
  status: string
  actions: DesktopVideoAction[]
  proposals?: DesktopVideoProposal[]
  created_at: string
  is_current: boolean
}

export interface DesktopVideoState {
  current: DesktopVideoSet | null
  fallback?: DesktopVideoSet | null
  desired_context_hash: string | null
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
  set_id: number
  action_id: number
  set_epoch: number
  kind: DesktopVideoKind
  continuity: DesktopVideoContinuity
  base_version: number | null
  expires_at: string
  video_url: string
  poster_url: string | null
  version: number
}

export type DesktopVideoReceiptStatus = 'started' | 'completed' | 'interrupted' | 'failed' | 'rejected'
