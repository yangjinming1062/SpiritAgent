// `window.spiritagent` 渲染层 API 面的 ambient 声明。
//
// 唯一的"载荷类型"与"通道名"真理源在 `@ipc/contracts` (`client/shared/ipc/contracts.ts`)。
// 本文件只描述 *形状*:通道名 → 方法签名。所有参数与返回类型通过
// `IpcInvokeContract[K]` / `IpcEventContract[K]` 查表引用契约,
// 任何契约字段变更会在两侧 `tsc --noEmit` 同时报错。
//
// `api` 是例外 — 保留 `<T = unknown>` 泛型,因为渲染层有 11+ 处
// `await window.spiritagent.api<MyResponse>(req)` 调用;契约
// `IpcInvokeContract['spiritagent:api']` 是非泛型的,直接查表会让泛型调用点全报错。

import type {
  DesktopActivatePayload,
  DesktopAuthBroadcast,
  DesktopAuthSnapshot,
  DesktopBootProgress,
  DesktopGatewayEvent,
  DesktopGatewayRpcRequest,
  DesktopGatewayRpcResponse,
  DesktopGatewayState,
  DesktopPrefsHydrated,
  DesktopRunnerState,
  DesktopRunnerStatusEvent,
  DesktopShortcutsConfig,
  DesktopShortcutsSetPayload,
  DesktopShortcutsState,
  DesktopSurfaceChangedEvent,
  DesktopSurfaceOpenPayload,
  DesktopUiThemeBroadcast,
  DesktopUpdateEvent,
  IpcEventContract,
  IpcInvokeContract,
  IpcSendContract,
  MediaSttPayload,
  MediaTtsPayload,
  RunnerConfigPatch,
  ShortcutRegistrationStatus,
  SpiritAgentApiRequest,
  SpiritAgentConnectionPublic,
  SpiritAgentSelectPathsOptions,
  SpiritAgentUiTheme
} from '@ipc/contracts'

// 把契约里的 `(payload) => R | Promise<R>` 收窄为 `(...args) => Promise<R>`,
// 渲染层所有调用点假设返回纯 `Promise<R>`(否则 `getGatewayWsUrl().then(...)`
// 会因联合类型无法识别 `.then` 报错;且 `() => R | Promise<R>` 协变于
// `() => Promise<R>`,反向赋值给期望 `() => Promise<R>` 的类型会失败)。
type AsyncIpc<T extends (...args: never[]) => unknown> = (...args: Parameters<T>) => Promise<Awaited<ReturnType<T>>>

// 事件订阅方法的形态辅助:`IpcEventContract[K]` 是 `[payload: T]` 元组(有载荷)
// 或 `[]`(空)。根据长度分支:
type EventSubscription<K extends keyof IpcEventContract> = IpcEventContract[K] extends [infer P]
  ? (callback: (payload: P) => void) => () => void
  : (callback: () => void) => () => void

export {}

declare global {
  interface Window {
    spiritagent: {
      getConnection: AsyncIpc<IpcInvokeContract['spiritagent:connection']>
      getGatewayWsUrl: AsyncIpc<IpcInvokeContract['spiritagent:gateway:ws-url']>
      gatewayRequest: <T = unknown>(payload: { method: string; params?: Record<string, unknown> }) => Promise<T>
      gatewayGetState: AsyncIpc<IpcInvokeContract['spiritagent:gateway:get-state']>
      gatewayBroadcastState: (state: DesktopGatewayState) => void
      gatewayBroadcastEvent: (event: DesktopGatewayEvent) => void
      gatewayRpcReply: (payload: DesktopGatewayRpcResponse) => void
      onGatewayStateChanged: EventSubscription<'spiritagent:gateway:state-changed'>
      onGatewayEvent: EventSubscription<'spiritagent:gateway:event'>
      onGatewayRpcDispatch: EventSubscription<'spiritagent:gateway:rpc-dispatch'>
      getBootProgress: AsyncIpc<IpcInvokeContract['spiritagent:boot-progress:get']>
      activate: AsyncIpc<IpcInvokeContract['spiritagent:auth:activate']>
      refreshSession: AsyncIpc<IpcInvokeContract['spiritagent:auth:refresh']>
      logout: AsyncIpc<IpcInvokeContract['spiritagent:auth:logout']>
      getSession: AsyncIpc<IpcInvokeContract['spiritagent:auth:get-session']>
      api: <T = unknown>(request: SpiritAgentApiRequest) => Promise<T>
      /** 把后端服务的二进制资产以 data URL 的形式取回(见 connection.cjs)。 */
      apiAsset: AsyncIpc<IpcInvokeContract['spiritagent:api:asset']>
      /** 把后端服务的二进制资产以原始字节取回——用于大体积负载(GLB),
       * 不能接受 base64 膨胀。支持通过 contentHash 做磁盘缓存。 */
      apiAssetBuffer: AsyncIpc<IpcInvokeContract['spiritagent:api:asset-buffer']>
      /** 获取缓存的模型流媒体协议 URL(spiritagent-media://...),
       * 供前端零拷贝流式加载。 */
      apiAssetModelUrl: AsyncIpc<IpcInvokeContract['spiritagent:api:asset-model-url']>
      readFileDataUrl: AsyncIpc<IpcInvokeContract['spiritagent:readFileDataUrl']>
      /** 聊天图片附件读取：超限降采样重编码，产出可直接发送的 data URL。 */
      readImageForAttach: AsyncIpc<IpcInvokeContract['spiritagent:readImageForAttach']>
      /** 聊天视频附件上传：主进程读文件经后端 /api/media/videos 换取会话级附件 URL。 */
      uploadVideoForAttach: AsyncIpc<IpcInvokeContract['spiritagent:media:video-upload']>
      selectPaths: AsyncIpc<IpcInvokeContract['spiritagent:selectPaths']>
      writeClipboard: AsyncIpc<IpcInvokeContract['spiritagent:writeClipboard']>
      log: AsyncIpc<IpcInvokeContract['spiritagent:log:emit']>
      runnerInvoke: AsyncIpc<IpcInvokeContract['spiritagent:runner:invoke']>
      runnerCancel: AsyncIpc<IpcInvokeContract['spiritagent:runner:cancel']>
      runnerGetState: AsyncIpc<IpcInvokeContract['spiritagent:runner:get-state']>
      runnerGetTools: AsyncIpc<IpcInvokeContract['spiritagent:runner:get-tools']>
      setUiTheme: (payload: IpcSendContract['spiritagent:ui-theme'][0]) => void
      prefs: {
        set: (payload: IpcSendContract['spiritagent:prefs:set'][0]) => void
      }
      shortcuts: {
        get: AsyncIpc<IpcInvokeContract['spiritagent:shortcuts:get']>
        onChanged: EventSubscription<'spiritagent:shortcuts:changed'>
        set: AsyncIpc<IpcInvokeContract['spiritagent:shortcuts:set']>
      }
      surface: {
        open: AsyncIpc<IpcInvokeContract['spiritagent:surface:open']>
        close: AsyncIpc<IpcInvokeContract['spiritagent:surface:close']>
        isMaximized: AsyncIpc<IpcInvokeContract['spiritagent:surface:is-maximized']>
        maximize: AsyncIpc<IpcInvokeContract['spiritagent:surface:maximize']>
        minimize: AsyncIpc<IpcInvokeContract['spiritagent:surface:minimize']>
        getState: AsyncIpc<IpcInvokeContract['spiritagent:surface:get-state']>
        setIgnoreMouseEvents: AsyncIpc<IpcInvokeContract['spiritagent:surface:set-ignore-mouse-events']>
        onChanged: EventSubscription<'spiritagent:surface:changed'>
      }
      chat: {
        onPendingFeed: EventSubscription<'spiritagent:chat:pending-feed'>
        setPendingFeed: AsyncIpc<IpcInvokeContract['spiritagent:chat:set-pending-feed']>
        takePendingFeed: AsyncIpc<IpcInvokeContract['spiritagent:chat:take-pending-feed']>
      }
      runnerConfig: {
        read: AsyncIpc<IpcInvokeContract['spiritagent:runner-config:read']>
        write: AsyncIpc<IpcInvokeContract['spiritagent:runner-config:write']>
        patch: AsyncIpc<IpcInvokeContract['spiritagent:runner-config:patch']>
      }
      skills: {
        list: AsyncIpc<IpcInvokeContract['spiritagent:skills:list']>
        setEnabled: AsyncIpc<IpcInvokeContract['spiritagent:skill:set-enabled']>
      }
      toolsets: {
        list: AsyncIpc<IpcInvokeContract['spiritagent:toolsets:list']>
        setEnabled: AsyncIpc<IpcInvokeContract['spiritagent:toolset:set-enabled']>
      }
      media: {
        stt: AsyncIpc<IpcInvokeContract['spiritagent:media:stt']>
        tts: AsyncIpc<IpcInvokeContract['spiritagent:media:tts']>
        onboardingAudio: {
          read: AsyncIpc<IpcInvokeContract['spiritagent:onboardingAudio:read']>
        }
      }
      sprite: {
        hide: AsyncIpc<IpcInvokeContract['spiritagent:sprite:hide']>
        setIgnoreMouseEvents: AsyncIpc<IpcInvokeContract['spiritagent:sprite:set-ignore-mouse-events']>
        getPosition: AsyncIpc<IpcInvokeContract['spiritagent:sprite:get-position']>
        setPosition: AsyncIpc<IpcInvokeContract['spiritagent:sprite:set-position']>
        moveToCursorDisplay: AsyncIpc<IpcInvokeContract['spiritagent:sprite:move-to-cursor-display']>
      }
      onPowerResume: EventSubscription<'spiritagent:power-resume'>
      onBootProgress: EventSubscription<'spiritagent:boot-progress'>
      onSessionExpired: EventSubscription<'spiritagent:auth:session-expired'>
      onAuthChanged: EventSubscription<'spiritagent:auth:changed'>
      onRunnerStatus: EventSubscription<'spiritagent:runner:status'>
      onTrayLogout: EventSubscription<'spiritagent:tray:logout'>
      onTrayActivate: EventSubscription<'spiritagent:tray:activate'>
      onTrayResetPosition: EventSubscription<'spiritagent:tray:reset-position'>
      onUiThemeChanged: EventSubscription<'spiritagent:ui-theme-changed'>
      onPrefsHydrated: EventSubscription<'spiritagent:prefs-hydrated'>
      getVersion: AsyncIpc<IpcInvokeContract['spiritagent:version']>
      update: {
        check: AsyncIpc<IpcInvokeContract['spiritagent:update:check']>
        onEvent: EventSubscription<'spiritagent:update-event'>
      }
    }
    spiritagentWebUtils?: {
      getPathForFile: (file: File) => string
    }
  }
}

// 显式重新导出,避免 60+ 处 `window.spiritagent` 调用方需要重写导入;
// `expectTypeOf<IpcInvokeContract[K]>()` 之类的类型测试也可以从同一个模块导入。
export type {
  DesktopActivatePayload,
  DesktopAuthBroadcast,
  DesktopAuthSnapshot,
  DesktopBootProgress,
  type DesktopGatewayEvent,
  type DesktopGatewayRpcRequest,
  type DesktopGatewayRpcResponse,
  type DesktopGatewayState,
  type DesktopPrefsHydrated,
  type DesktopRunnerState,
  type DesktopRunnerStatusEvent,
  type DesktopShortcutsConfig,
  type DesktopShortcutsSetPayload,
  type DesktopShortcutsState,
  type DesktopSurfaceChangedEvent,
  type DesktopSurfaceOpenPayload,
  type DesktopUiThemeBroadcast,
  type DesktopUpdateEvent,
  type IpcEventContract,
  type IpcInvokeContract,
  type IpcSendContract,
  type MediaSttPayload,
  type MediaTtsPayload,
  type RunnerConfigPatch,
  type ShortcutRegistrationStatus,
  type SpiritAgentApiRequest,
  type SpiritAgentConnectionPublic,
  SpiritAgentPrefsSet,
  SpiritAgentSelectPathsOptions,
  SpiritAgentUiTheme
}
