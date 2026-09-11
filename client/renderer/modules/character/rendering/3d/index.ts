export { resolveClip } from './AnimationMap'
export { Companion3D } from './companion-3d'
export { $engineFps, $powerProfile, $rendererBackend } from './engine-diagnostics'
export { createGLTFLoader } from './gltf-loader-factory'
export {
  $availableClipNames,
  $clipMap,
  $glbLoadFailed,
  $modelGenError,
  $modelGenProgress,
  $modelGenState,
  $modelInfo,
  clearModelRetry,
  hydrateModel,
  setModelFailed,
  setModelInfo
} from './model-store'
export { type RigType, SUPPORTED_RIG_TYPES } from './rig'
export { $sprite3DHitTest } from './silhouette-hit'
