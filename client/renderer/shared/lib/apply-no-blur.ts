// no-blur 降级管理：OS 减透明偏好、集显探测、用户手动开关与帧预算自动降级
// 四个来源汇成一个 html class。前两个在启动时一次性判定（探测成本高且不会变化）；
// 手动开关经 companion 偏好管道跨窗口、跨端同步；帧预算由各表面窗口的
// glass-budget 监视器在运行期驱动（见 initGlassBudgetGuard）。
// 任一来源命中即降级；自动降级会在来源消失后恢复，手动开关则始终生效。

import { atom } from 'nanostores'

import {
  persistBoolean,
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedBoolean
} from '@/shared/lib/storage'

const PREF_KEY = registerCompanionStorageKey('da.companion.reduceTransparency')
const BUDGET_FRAME_MS = 1000 / 45
const DEGRADE_WINDOW_MS = 2000
const RECOVER_WINDOW_MS = 10000

export const $manualReduceTransparency = atom<boolean>(storedBoolean(PREF_KEY, false))
export const $noBlur = atom<boolean>(false)
const $autoGlassDegraded = atom<boolean>(false)

let wired = false
let isIntegratedGpuResult = false

registerStorageClearHandler(() => {
  $manualReduceTransparency.set(false)
  persistString(PREF_KEY, null)
})

function applyNoBlur(degraded: boolean): void {
  document.documentElement.classList.toggle('no-blur', degraded)
  $noBlur.set(degraded)
}

/** 启动判定 + 订阅动态来源；每个窗口的 entry 模块作用域调用一次。 */
function initNoBlur(): void {
  if (wired) {
    return
  }

  wired = true

  applyNoBlur(
    prefersReducedTransparency() || isIntegratedGpuResult || $manualReduceTransparency.get() || $autoGlassDegraded.get()
  )

  $manualReduceTransparency.listen(degraded => {
    applyNoBlur(degraded || prefersReducedTransparency() || isIntegratedGpuResult || $autoGlassDegraded.get())
  })

  $autoGlassDegraded.listen(degraded => {
    applyNoBlur(degraded || prefersReducedTransparency() || isIntegratedGpuResult || $manualReduceTransparency.get())
  })
}

export function applyNoBlurIfNeeded(): void {
  isIntegratedGpuResult = isIntegratedGpu()
  initNoBlur()
}

export function setManualReduceTransparency(value: boolean): void {
  hydrateManualReduceTransparency(value)
  window.spiritagent?.prefs?.set({ key: 'companion.reduce_transparency', value })
}

/** 水合与跨窗口回声只更新缓存，不重复上传或在登出清理时写入旧用户偏好。 */
export function hydrateManualReduceTransparency(value: boolean): void {
  $manualReduceTransparency.set(value)
  persistBoolean(PREF_KEY, value)
}

function prefersReducedTransparency(): boolean {
  return window.matchMedia('(prefers-reduced-transparency: reduce)').matches
}

function isIntegratedGpu(): boolean {
  const canvas = document.createElement('canvas')
  const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')

  if (!gl) {
    return true
  }

  const ext = gl.getExtension('WEBGL_debug_renderer_info')

  const raw = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)

  const renderer = String(raw).toLowerCase()
  gl.getExtension('WEBGL_lose_context')?.loseContext()

  if (/nvidia|geforce|rtx|radeon rx|radeon pro|apple gpu|apple m\d/.test(renderer)) {
    return false
  }

  return /intel|uhd|iris|hd graphics|radeon graphics|mali|adreno|swiftshader|llvmpipe|microsoft basic/.test(renderer)
}

// ── 帧预算自动降级 ──
// 大面积 backdrop-filter 采样桌面时若合成持续超预算，撤销玻璃比
// 让整个表面积互动掉帧更可取。观察窗为 2s：连续超时才降级（抖动不触发），
// 恢复窗口放宽到 10s 连续达标（滞回，避免临界负载来回切换）。
/**
 * 表面窗口（living/workbench）挂载后启动的 rAF 帧间隔监视。
 * 返回停止函数。精灵窗不调用——模型引擎有自己的功率档位调度。
 */
export function initGlassBudgetGuard(): () => void {
  let last = 0
  let overAccum = 0
  let okAccum = 0

  const resetSample = (): void => {
    last = 0
    overAccum = 0
    okAccum = 0
  }

  const tick = (now: number): void => {
    if (document.hidden) {
      resetSample()
    } else if (last > 0) {
      const delta = now - last

      if (delta > BUDGET_FRAME_MS) {
        overAccum += delta
        okAccum = 0
      } else {
        okAccum += delta
        overAccum = 0
      }

      if (overAccum >= DEGRADE_WINDOW_MS) {
        overAccum = 0
        okAccum = 0
        $autoGlassDegraded.set(true)
      } else if (okAccum >= RECOVER_WINDOW_MS) {
        overAccum = 0
        okAccum = 0
        $autoGlassDegraded.set(false)
      }
    }

    last = document.hidden ? 0 : now
    rafId = requestAnimationFrame(tick)
  }

  let rafId = requestAnimationFrame(tick)
  document.addEventListener('visibilitychange', resetSample)

  return () => {
    cancelAnimationFrame(rafId)
    document.removeEventListener('visibilitychange', resetSample)
    $autoGlassDegraded.set(false)
  }
}
