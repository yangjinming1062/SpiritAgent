export {
  $desktopVideoContextRevision,
  $desktopVideoError,
  $desktopVideoLoading,
  $desktopVideoPlayback,
  $desktopVideoSets,
  $desktopVideoState,
  acknowledgeDesktopVideoPlay,
  claimDesktopVideoPlay,
  designDesktopVideoAction,
  ensureDesktopVideoCurrent,
  generateDesktopVideoAction,
  getDesktopVideoPrompt,
  handleDesktopVideoEvent,
  notifyDesktopVideoContextChanged,
  playDesktopVideoAction,
  refreshDesktopVideos,
  reviewDesktopVideoAction,
  setDesktopVideoPreferences,
  uploadDesktopVideoAction
} from './desktop-video-store'
export type {
  DesktopVideoAction,
  DesktopVideoKind,
  DesktopVideoPlayCommand,
  DesktopVideoPrompt,
  DesktopVideoPromptReference,
  DesktopVideoProposal,
  DesktopVideoReceiptStatus,
  DesktopVideoSet,
  DesktopVideoState
} from './types'
