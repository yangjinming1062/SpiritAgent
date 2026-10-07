import type { ActiveTurnSnapshot, SessionMessage, SessionResumeResponse } from '@protocol'

export type { ChatAttachment, CompanionBubble, SessionInfo, SessionMessage, SystemPresetSummary } from '@protocol'

export interface RemoteSession {
  user: { id: number; username: string }
  device: {
    id: number
    name: string
    expires_at: string
    last_seen_at: string | null
  }
  csrf_token: string
}

export type ActiveTurn = ActiveTurnSnapshot
export type Resume = SessionResumeResponse

export interface History {
  messages: SessionMessage[]
  next_cursor: number | null
  has_more: boolean
}

export interface Comment {
  id: string
  role: 'user' | 'companion'
  content: string
  reply_status: string
  reply_error: string | null
}

export interface Post {
  id: string
  title: string
  body: string
  published_at: string
  content_type: 'text' | 'image' | 'video' | 'audio'
  media_url: string | null
  audio_url: string | null
  comments: Comment[]
}

export interface Recovery {
  publication_id: string
  title: string
  status: string
  video_status: string
  media_url: string | null
  error: string | null
  can_adopt: boolean
  can_discard: boolean
}

export interface Diary {
  id: string
  entry_date: string
  title: string
  body: string
  mood: string | null
}

export interface Memory {
  id: number
  content: string | null
  context: string | null
  content_version: number
  updated_at: string | null
}

export interface Scene {
  id: number
  title: string
  description: string
  requirements: string
  prompt: string
  url: string
  status: string
  stage: string
  error: string | null
  regeneration: { status: string; stage: string; error: string | null } | null
}

export interface Outfit {
  id: number
  name: string
  description: string | null
  fullbody_url: string
  status: string
  active: boolean
  description_status: string
  description_error: string | null
  initial_video_error: string | null
}

export interface VideoPack {
  id: number
  outfit_id: number | null
  status: string
  active: boolean
  can_retry: boolean
  error: string | null
}
