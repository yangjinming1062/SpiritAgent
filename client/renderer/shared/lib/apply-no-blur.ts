export function applyNoBlurIfNeeded(): void {
  if (prefersReducedTransparency() || isIntegratedGpu()) {
    document.documentElement.classList.add('no-blur')
  }
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
