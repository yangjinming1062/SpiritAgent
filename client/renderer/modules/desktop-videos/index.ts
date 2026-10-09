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
  handleDesktopVideoEvent,
  notifyDesktopVideoContextChanged,
  playDesktopVideoAction,
  refreshDesktopVideos,
  reviewDesktopVideoAction,
  setDesktopVideoPreferences
} from './desktop-video-store'
export type {
  DesktopVideoAction,
  DesktopVideoKind,
  DesktopVideoPlayCommand,
  DesktopVideoProposal,
  DesktopVideoReceiptStatus,
  DesktopVideoSet,
  DesktopVideoState
} from './types'
