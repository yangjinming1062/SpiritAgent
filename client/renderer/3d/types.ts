/** GPU 功耗偏好——`'low-power'` 走 iGPU 兜底，`'high-performance'` 显式唤醒 dGPU；故意排除 `'default'`。 */
type EnginePowerPreference = 'high-performance' | 'low-power'

export interface EngineOptions {
  container?: HTMLElement
  /** Whether the renderer should enable shadow mapping. Default `false` — for a 300×360 desktop-pet window PBR environment lighting alone conveys depth, and a 2048² PCFSoft shadow map is the single biggest GPU cost in the pipeline. When enabled, capped at 1024² PCF. */
  useShadows?: boolean
  /** 默认 `'low-power'`，避免在双显卡笔记本上唤醒 dGPU；调试工具经此显式覆盖。 */
  powerPreference?: EnginePowerPreference
}

export type EngineBackendKind = 'webgpu' | 'webgl2' | 'classic-webgl'

export interface LoadedModelInfo {
  hasAnimations: boolean
  clipNames: string[]
  /** True when load() fell through to the procedural egg (no bytes / parse failed). */
  procedural: boolean
}
