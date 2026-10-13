import type { DesktopBackgroundBridge, DesktopBridge, DesktopFileBridge } from '@client-shared/ipc/bridge'

type BackendPort = Pick<DesktopBridge, 'api' | 'apiAsset' | 'apiAssetBuffer' | 'sessionHistory'>
type AccountPort = Pick<
  DesktopBridge,
  'activate' | 'refreshSession' | 'logout' | 'getSession' | 'onAuthChanged' | 'onSessionExpired'
>
type GatewayPort = Pick<
  DesktopBridge,
  | 'getGatewayWsUrl'
  | 'gatewayRequest'
  | 'gatewayGetState'
  | 'gatewayBroadcastState'
  | 'gatewayBroadcastEvent'
  | 'gatewayRpcReply'
  | 'onGatewayStateChanged'
  | 'onGatewayEvent'
  | 'onGatewayRpcDispatch'
>
type RunnerPort = Pick<
  DesktopBridge,
  | 'runnerInvoke'
  | 'runnerDispatchCall'
  | 'runnerCancel'
  | 'runnerGetState'
  | 'runnerGetTools'
  | 'onRunnerStatus'
  | 'runnerConfig'
  | 'skills'
  | 'toolsets'
>
type FilePort = Pick<
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
type ShellPort = Pick<
  DesktopBridge,
  'presentation' | 'dock' | 'desktop' | 'surface' | 'sprite' | 'chat' | 'onTrayActivate' | 'onTrayResetPosition'
>
type PreferencesPort = Pick<
  DesktopBridge,
  'prefs' | 'shortcuts' | 'setUiTheme' | 'onUiThemeChanged' | 'onPrefsHydrated'
>
type SystemPort = Pick<DesktopBridge, 'log' | 'onPowerResume' | 'getVersion' | 'update'>
type MediaPort = Pick<DesktopBridge, 'media' | 'voicePlayback'>
type EventPort = Pick<DesktopBridge, Extract<keyof DesktopBridge, `on${string}`>>

// 能力按调用用途收窄，延迟读取 preload；保留原桥的调用与订阅语义。
export const backendApi = (): BackendPort => window.spiritagent
export const accountApi = (): AccountPort => window.spiritagent
export const gatewayApi = (): GatewayPort => window.spiritagent
export const runnerApi = (): RunnerPort => window.spiritagent
export const fileApi = (): FilePort => window.spiritagent
export const shellApi = (): ShellPort => window.spiritagent
export const preferencesApi = (): PreferencesPort => window.spiritagent
export const systemApi = (): SystemPort => window.spiritagent
export const mediaApi = (): MediaPort => window.spiritagent
export const desktopEvents = (): EventPort => window.spiritagent

export const gatewayHost = (): GatewayPort &
  Pick<RunnerPort, 'runnerGetTools' | 'onRunnerStatus'> &
  Pick<SystemPort, 'onPowerResume'> => window.spiritagent
export const activityHost = (): Pick<RunnerPort, 'runnerInvoke'> & Pick<ShellPort, 'sprite'> => window.spiritagent

export const backgroundMedia = (): DesktopBackgroundBridge | undefined => window.desktopBackground
export const desktopFilePaths = (): DesktopFileBridge | undefined => window.spiritagentWebUtils
