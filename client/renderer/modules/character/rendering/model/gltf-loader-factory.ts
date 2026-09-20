import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js'
import type { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

import { getDracoLoader } from './draco-loader'

let gltfLoaderInstance: GLTFLoader | null = null

/**
 * Returns a cached singleton GLTFLoader configured with MeshoptDecoder and DRACOLoader.
 * If DRACOLoader was not ready on first call, subsequent calls check and attach it.
 */
export function createGLTFLoader(): GLTFLoader {
  if (!gltfLoaderInstance) {
    const loader = new GLTFLoader()

    if (typeof MeshoptDecoder !== 'undefined') {
      try {
        loader.setMeshoptDecoder(MeshoptDecoder)
      } catch {
        /* fallback if wasm initialization fails */
      }
    }

    gltfLoaderInstance = loader
  }

  const draco = getDracoLoader()

  if (draco && (gltfLoaderInstance as unknown as { dracoLoader: DRACOLoader | null }).dracoLoader !== draco) {
    gltfLoaderInstance.setDRACOLoader(draco)
  }

  return gltfLoaderInstance
}
