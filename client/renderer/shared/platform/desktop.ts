import type { DesktopBackgroundBridge, DesktopBridge, DesktopFileBridge } from '@client-shared/ipc/bridge'
import type { DesktopApiOperationKey, DesktopApiResult, SpiritAgentApiRequest } from '@ipc/contracts'

import type { ApiResponse } from '../../../../shared/api'
import { ApiError } from '../../../../shared/errors'

type ApiValueCall<F> = F extends (...args: infer Args) => Promise<DesktopApiResult<infer Value>>
  ? (...args: Args) => Promise<Value>
  : never

type BackendPort = Pick<DesktopBridge, 'apiAsset' | 'apiAssetBuffer' | 'sessionHistory'> & {
  api: <Operation extends DesktopApiOperationKey>(
    request: SpiritAgentApiRequest<Operation>
  ) => Promise<ApiResponse<Operation>>
}
type AccountPort = Pick<
  DesktopBridge,
  'activate' | 'refreshSession' | 'logout' | 'getSession' | 'onAuthChanged' | 'onSessionExpired'
>
type GatewayPort = Pick<
  DesktopBridge,
  'gatewayPresence' | 'gatewayGetState' | 'onGatewayStateChanged' | 'onGatewayEvent'
>
type RunnerPort = Pick<
  DesktopBridge,
  'runnerInvoke' | 'runnerGetState' | 'runnerGetTools' | 'onRunnerStatus' | 'runnerConfig' | 'skills' | 'toolsets'
>
type FileBridgePort = Pick<
  DesktopBridge,
  | 'readFileDataUrl'
  | 'readImageForAttach'
  | 'uploadVideoForAttach'
  | 'uploadDesktopVideo'
  | 'selectPaths'
  | 'writeClipboard'
  | 'copyImage'
  | 'saveImage'
>
type MediaUpload = 'uploadVideoForAttach' | 'uploadDesktopVideo'
type FilePort = Omit<FileBridgePort, MediaUpload> & {
  [K in MediaUpload]: ApiValueCall<FileBridgePort[K]>
}

type ShellPort = Pick<
  DesktopBridge,
  | 'uiState'
  | 'presentation'
  | 'dock'
  | 'desktop'
  | 'surface'
  | 'sprite'
  | 'chat'
  | 'onTrayActivate'
  | 'onTrayResetPosition'
>
type PreferencesPort = Pick<
  DesktopBridge,
  'prefs' | 'shortcuts' | 'setUiTheme' | 'onUiThemeChanged' | 'onPrefsHydrated'
>
type SystemPort = Pick<DesktopBridge, 'log' | 'onPowerResume' | 'getVersion' | 'update'>
type MediaPort = Pick<DesktopBridge, 'voicePlayback'> & {
  media: Pick<DesktopBridge['media'], 'onboardingAudio'> & {
    [K in 'stt' | 'tts' | 'uploadAudio' | 'uploadVoiceSample']: ApiValueCall<DesktopBridge['media'][K]>
  }
}
type EventPort = Pick<DesktopBridge, Extract<keyof DesktopBridge, `on${string}`>>

async function apiValue<T>(response: Promise<DesktopApiResult<T>>): Promise<T> {
  const result = await response

  if (!result.ok) {
    throw new ApiError(result.status, result.error)
  }

  return result.value
}

// 能力按调用用途收窄，延迟读取 preload；保留原桥的调用与订阅语义。
export const backendApi = (): BackendPort => ({
  api: <Operation extends DesktopApiOperationKey>(request: SpiritAgentApiRequest<Operation>) =>
    apiValue(window.spiritagent.api(request)),
  apiAsset: window.spiritagent.apiAsset,
  apiAssetBuffer: window.spiritagent.apiAssetBuffer,
  sessionHistory: window.spiritagent.sessionHistory
})
export const accountApi = (): AccountPort => window.spiritagent
export const gatewayApi = (): GatewayPort => window.spiritagent
export const runnerApi = (): RunnerPort => window.spiritagent
export const fileApi = (): FilePort => ({
  readFileDataUrl: window.spiritagent.readFileDataUrl,
  readImageForAttach: window.spiritagent.readImageForAttach,
  uploadVideoForAttach: payload => apiValue(window.spiritagent.uploadVideoForAttach(payload)),
  uploadDesktopVideo: payload => apiValue(window.spiritagent.uploadDesktopVideo(payload)),
  selectPaths: window.spiritagent.selectPaths,
  writeClipboard: window.spiritagent.writeClipboard,
  copyImage: window.spiritagent.copyImage,
  saveImage: window.spiritagent.saveImage
})
export const desktopPlatform = (): string => window.spiritagent.platform
export const shellApi = (): ShellPort => window.spiritagent
export const preferencesApi = (): PreferencesPort => window.spiritagent
export const systemApi = (): SystemPort => window.spiritagent
export const mediaApi = (): MediaPort => ({
  media: {
    onboardingAudio: window.spiritagent.media.onboardingAudio,
    stt: payload => apiValue(window.spiritagent.media.stt(payload)),
    tts: payload => apiValue(window.spiritagent.media.tts(payload)),
    uploadAudio: payload => apiValue(window.spiritagent.media.uploadAudio(payload)),
    uploadVoiceSample: payload => apiValue(window.spiritagent.media.uploadVoiceSample(payload))
  },
  voicePlayback: window.spiritagent.voicePlayback
})
export const desktopEvents = (): EventPort => window.spiritagent

export const activityHost = (): Pick<RunnerPort, 'runnerInvoke'> & Pick<ShellPort, 'sprite'> => window.spiritagent

export const backgroundMedia = (): DesktopBackgroundBridge | undefined => window.desktopBackground
export const desktopFilePaths = (): DesktopFileBridge | undefined => window.spiritagentWebUtils
