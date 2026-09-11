/** vendor UMD 脚本加载器 — `?url` 导入让 vite 产哈希资产，运行时按序注入经典 script。
 * UMD 落到 window.Rigger / window.agPsd / window.GenericParts（见 puppet-types 的全局声明）。 */

import agPsdUrl from './vendor/ag-psd.min.js?url'
import genericPartsUrl from './vendor/genericparts.js?url'
import riggerUrl from './vendor/rigger.js?url'

let vendorReady: Promise<void> | null = null

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = src
    s.onload = () => resolve()
    s.onerror = () => reject(new Error(`vendor script failed: ${src}`))
    document.head.appendChild(s)
  })
}

export function ensureVendorLibs(): Promise<void> {
  vendorReady ??= (async () => {
    if (window.Rigger && window.agPsd && window.GenericParts) {
      return
    }

    await Promise.all([loadScript(agPsdUrl), loadScript(riggerUrl), loadScript(genericPartsUrl)])
  })()

  return vendorReady
}
