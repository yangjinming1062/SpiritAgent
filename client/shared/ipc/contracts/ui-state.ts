import type { AppNavigationTarget } from '../desktop-presentation'
import type {
  AppearanceUiSnapshot,
  CompanionEditorUiSnapshot,
  DiaryUiSnapshot,
  LifeUiSnapshot,
  MainConversationDraftSnapshot,
  SceneUiSnapshot,
  VisualsUiSnapshot,
  WardrobeDesignUiSnapshot
} from '../ui-state'

export type DesktopSurfaceOpenPayload = AppNavigationTarget

export interface MainUiStateData {
  navigation: AppNavigationTarget
  life: LifeUiSnapshot
  diary: DiaryUiSnapshot
  conversation: MainConversationDraftSnapshot
  visuals: VisualsUiSnapshot
  appearance: AppearanceUiSnapshot
  scene: SceneUiSnapshot
  wardrobeDesign: WardrobeDesignUiSnapshot
  companionEditors: CompanionEditorUiSnapshot
}

export interface MainUiStateSnapshot extends MainUiStateData {
  accountId: string
  authSessionId: string
  revision: number
}

export interface MainUiStateUpdate extends MainUiStateData {
  authSessionId: string
}

export interface UiInputBusyState {
  recording: boolean
  uploading: boolean
}

export type UiStateRequest =
  | { kind: 'capture'; requestId: string; authSessionId: string }
  | { kind: 'restore'; requestId: string; authSessionId: string; snapshot: MainUiStateSnapshot }

export type UiStateReply =
  | { kind: 'capture'; requestId: string; authSessionId: string; data: MainUiStateData }
  | { kind: 'capture'; requestId: string; authSessionId: string; error: string }
  | { kind: 'restore'; requestId: string; authSessionId: string; error?: string }
