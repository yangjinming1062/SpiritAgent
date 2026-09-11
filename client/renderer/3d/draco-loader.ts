import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js'

import dracoDecoderWasmUrl from '../../assets/draco/draco_decoder.wasm?url'
import dracoWasmWrapperUrl from '../../assets/draco/draco_wasm_wrapper.js?url'

let dracoLoaderInstance: DRACOLoader | null = null

export function getDracoLoader(): DRACOLoader | null {
  if (!dracoLoaderInstance) {
    try {
      dracoLoaderInstance = new DRACOLoader()
      dracoLoaderInstance.setDecoderPath({
        js: dracoWasmWrapperUrl,
        wasm: dracoDecoderWasmUrl
      })
      dracoLoaderInstance.setDecoderConfig({ type: 'wasm' })
      dracoLoaderInstance.preload()
    } catch {
      dracoLoaderInstance = null
    }
  }

  return dracoLoaderInstance
}
