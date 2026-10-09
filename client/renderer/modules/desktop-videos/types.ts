export type DesktopVideoKind = 'loop' | 'once'

export interface DesktopVideoAction {
  id: number
  set_id: number
  key: string
  name: string
  description: string
  kind: DesktopVideoKind
  duration_seconds: number
  status: 'queued' | 'processing' | 'ready' | 'failed' | 'review_pending'
  stage: string
  error: string | null
  enabled: boolean
  preset: boolean
  use_when: string[]
  avoid_when: string[]
  poster_url: string | null
  video_url: string | null
  candidate_video_url?: string | null
  candidate_poster_url?: string | null
  version: number
  actual_duration_ms: number | null
}

export interface DesktopVideoSet {
  id: number
  title: string
  outfit_id: number | null
  scene_id: number | null
  context_hash: string
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
  expires_at: string
  video_url: string
  poster_url: string | null
  version: number
}

export type DesktopVideoReceiptStatus = 'started' | 'completed' | 'interrupted' | 'failed' | 'rejected'
