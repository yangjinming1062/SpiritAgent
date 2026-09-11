import { clamp } from '@runtime'

export interface ShapeKey {
  value: number
  offsets: number[]
  opacityDelta?: number
}

export interface ParametricModel {
  width: number
  height: number
  parameters: Record<string, { min: number; max: number; initial: number; smoothing: number }>
  layers: {
    id: string
    texture: string
    vertices: number[]
    uvs: number[]
    triangles: number[]
    opacity: number
    order: number
    shapes: Record<string, ShapeKey[]>
  }[]
}

interface RenderLayer {
  model: ParametricModel['layers'][number]
  vertices: Float32Array
  vertexBuffer: WebGLBuffer
  uvBuffer: WebGLBuffer
  indexBuffer: WebGLBuffer
  texture: WebGLTexture
  opacity: number
}

const VERTEX = `attribute vec2 position; attribute vec2 uv;
uniform vec2 size; varying vec2 texCoord;
void main(){ texCoord=uv; gl_Position=vec4(position.x/size.x*2.0-1.0,1.0-position.y/size.y*2.0,0.0,1.0); }`

const FRAGMENT = `precision mediump float; varying vec2 texCoord;
uniform sampler2D image; uniform float opacity;
void main(){ vec4 c=texture2D(image,texCoord)*opacity; if(c.a<0.0039) discard; gl_FragColor=c; }`

// 关键形状必须由资产提供；运行时不从骨骼角度猜测肘部轮廓或手型。
export class ParametricRenderer {
  private readonly gl: WebGLRenderingContext
  private readonly program: WebGLProgram
  private readonly position: number
  private readonly uv: number
  private readonly opacity: WebGLUniformLocation | null
  private readonly layers: RenderLayer[] = []
  private readonly values: Record<string, number> = {}
  private readonly targets: Record<string, number> = {}
  private readonly pixels = new Map<string, ImageData>()
  private disposed = false

  constructor(
    canvas: HTMLCanvasElement,
    private readonly model: ParametricModel,
    textures: ReadonlyMap<string, TexImageSource>
  ) {
    validate(model, textures)
    const probe = document.createElement('canvas')

    for (const [name, texture] of textures) {
      if (texture instanceof ImageData) {
        this.pixels.set(name, texture)
      } else {
        probe.width = model.width
        probe.height = model.height
        const context = probe.getContext('2d')!
        context.drawImage(texture as CanvasImageSource, 0, 0, model.width, model.height)
        this.pixels.set(name, context.getImageData(0, 0, model.width, model.height))
      }
    }

    const gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true })

    if (!gl) {
      throw new Error('WebGL unavailable')
    }

    this.gl = gl

    const compile = (type: number, source: string): WebGLShader => {
      const shader = gl.createShader(type)!
      gl.shaderSource(shader, source)
      gl.compileShader(shader)

      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const error = gl.getShaderInfoLog(shader)
        gl.deleteShader(shader)
        throw new Error(error ?? 'Shader compilation failed')
      }

      return shader
    }

    const vertex = compile(gl.VERTEX_SHADER, VERTEX)

    try {
      const fragment = compile(gl.FRAGMENT_SHADER, FRAGMENT)

      try {
        this.program = gl.createProgram()!
        gl.attachShader(this.program, vertex)
        gl.attachShader(this.program, fragment)
        gl.linkProgram(this.program)
      } finally {
        gl.deleteShader(fragment)
      }
    } finally {
      gl.deleteShader(vertex)
    }

    if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) {
      const error = gl.getProgramInfoLog(this.program)
      gl.deleteProgram(this.program)
      throw new Error(error ?? 'Shader linking failed')
    }

    gl.useProgram(this.program)
    canvas.width = model.width
    canvas.height = model.height
    this.position = gl.getAttribLocation(this.program, 'position')
    this.uv = gl.getAttribLocation(this.program, 'uv')
    this.opacity = gl.getUniformLocation(this.program, 'opacity')
    gl.uniform2f(gl.getUniformLocation(this.program, 'size'), model.width, model.height)
    gl.uniform1i(gl.getUniformLocation(this.program, 'image'), 0)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true)

    for (const [id, param] of Object.entries(model.parameters)) {
      this.targets[id] = this.values[id] = param.initial
    }

    try {
      for (const layer of model.layers) {
        const texture = gl.createTexture()!
        gl.bindTexture(gl.TEXTURE_2D, texture)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)

        const state: RenderLayer = {
          model: layer,
          vertices: new Float32Array(layer.vertices),
          vertexBuffer: gl.createBuffer()!,
          uvBuffer: gl.createBuffer()!,
          indexBuffer: gl.createBuffer()!,
          texture,
          opacity: layer.opacity
        }

        this.layers.push(state)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, textures.get(layer.texture)!)
        gl.bindBuffer(gl.ARRAY_BUFFER, state.vertexBuffer)
        gl.bufferData(gl.ARRAY_BUFFER, state.vertices, gl.DYNAMIC_DRAW)
        gl.bindBuffer(gl.ARRAY_BUFFER, state.uvBuffer)
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(layer.uvs), gl.STATIC_DRAW)
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, state.indexBuffer)
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(layer.triangles), gl.STATIC_DRAW)
      }

      this.layers.sort((a, b) => a.model.order - b.model.order)
    } catch (error) {
      this.dispose()
      throw error
    }
  }

  setParameter(id: string, value: number): void {
    const parameter = this.model.parameters[id]

    if (!parameter || !Number.isFinite(value)) {
      throw new Error(`Invalid parameter: ${id}`)
    }

    this.targets[id] = clamp(value, parameter.min, parameter.max)
  }

  hitTest(x: number, y: number): boolean {
    if (this.disposed || x < 0 || y < 0 || x >= this.model.width || y >= this.model.height) {
      return false
    }

    const sample = (layer: RenderLayer): boolean => {
      if (layer.opacity < 0.01) {
        return false
      }

      const { triangles, uvs, texture } = layer.model
      const v = layer.vertices
      const pixels = this.pixels.get(texture)!

      for (let i = 0; i < triangles.length; i += 3) {
        const a = triangles[i]! * 2,
          b = triangles[i + 1]! * 2,
          c = triangles[i + 2]! * 2

        const ax = v[a]!,
          ay = v[a + 1]!,
          bx = v[b]!,
          by = v[b + 1]!,
          cx = v[c]!,
          cy = v[c + 1]!

        if (
          x < Math.min(ax, bx, cx) ||
          x > Math.max(ax, bx, cx) ||
          y < Math.min(ay, by, cy) ||
          y > Math.max(ay, by, cy)
        ) {
          continue
        }

        const det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)

        if (Math.abs(det) < 1e-8) {
          continue
        }

        const wa = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / det
        const wb = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / det
        const wc = 1 - wa - wb

        if (Math.min(wa, wb, wc) < -1e-6) {
          continue
        }

        const u = clamp(wa * uvs[a]! + wb * uvs[b]! + wc * uvs[c]!, 0, 1)
        const w = clamp(wa * uvs[a + 1]! + wb * uvs[b + 1]! + wc * uvs[c + 1]!, 0, 1)
        const px = Math.min(pixels.width - 1, Math.floor(u * pixels.width))
        const py = Math.min(pixels.height - 1, Math.floor(w * pixels.height))

        if (pixels.data[(py * pixels.width + px) * 4 + 3]! * layer.opacity > 8) {
          return true
        }
      }

      return false
    }

    return this.layers.some(sample)
  }

  frame(dt: number): void {
    if (this.disposed) {
      return
    }

    for (const [id, param] of Object.entries(this.model.parameters)) {
      const weight = param.smoothing > 0 ? 1 - Math.exp(-clamp(dt, 0, 0.1) / param.smoothing) : 1
      this.values[id]! += (this.targets[id]! - this.values[id]!) * weight
    }

    for (const layer of this.layers) {
      const source = layer.model
      layer.vertices.set(source.vertices)
      layer.opacity = source.opacity

      for (const [id, keys] of Object.entries(source.shapes)) {
        const value = this.values[id]!
        const upper = keys.findIndex(key => key.value >= value)
        const b = keys[upper < 0 ? keys.length - 1 : upper]!
        const a = keys[Math.max(0, upper < 0 ? keys.length - 1 : upper - 1)]!
        const t = a === b ? 0 : clamp((value - a.value) / (b.value - a.value), 0, 1)

        for (let k = 0; k < layer.vertices.length; k++) {
          layer.vertices[k]! += a.offsets[k]! * (1 - t) + b.offsets[k]! * t
        }

        layer.opacity += (a.opacityDelta ?? 0) * (1 - t) + (b.opacityDelta ?? 0) * t
      }

      layer.opacity = clamp(layer.opacity, 0, 1)
      this.gl.bindBuffer(this.gl.ARRAY_BUFFER, layer.vertexBuffer)
      this.gl.bufferSubData(this.gl.ARRAY_BUFFER, 0, layer.vertices)
    }

    this.draw()
  }

  private drawLayer(layer: RenderLayer): void {
    const gl = this.gl
    gl.bindBuffer(gl.ARRAY_BUFFER, layer.vertexBuffer)
    gl.enableVertexAttribArray(this.position)
    gl.vertexAttribPointer(this.position, 2, gl.FLOAT, false, 0, 0)
    gl.bindBuffer(gl.ARRAY_BUFFER, layer.uvBuffer)
    gl.enableVertexAttribArray(this.uv)
    gl.vertexAttribPointer(this.uv, 2, gl.FLOAT, false, 0, 0)
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, layer.indexBuffer)
    gl.bindTexture(gl.TEXTURE_2D, layer.texture)
    gl.uniform1f(this.opacity, layer.opacity)
    gl.drawElements(gl.TRIANGLES, layer.model.triangles.length, gl.UNSIGNED_SHORT, 0)
  }

  private draw(): void {
    const gl = this.gl
    gl.useProgram(this.program)
    gl.viewport(0, 0, this.model.width, this.model.height)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)

    for (const layer of this.layers) {
      if (layer.opacity > 0) {
        this.drawLayer(layer)
      }
    }
  }

  dispose(): void {
    if (this.disposed) {
      return
    }

    this.disposed = true

    for (const layer of this.layers) {
      this.gl.deleteTexture(layer.texture)
      this.gl.deleteBuffer(layer.vertexBuffer)
      this.gl.deleteBuffer(layer.uvBuffer)
      this.gl.deleteBuffer(layer.indexBuffer)
    }

    this.gl.deleteProgram(this.program)
    this.pixels.clear()
  }
}

function validate(model: ParametricModel, textures: ReadonlyMap<string, TexImageSource>): void {
  if (!Number.isInteger(model.width) || !Number.isInteger(model.height) || model.width <= 0 || model.height <= 0) {
    throw new Error('Invalid model dimensions')
  }

  for (const parameter of Object.values(model.parameters)) {
    if (
      !Object.values(parameter).every(Number.isFinite) ||
      parameter.min > parameter.max ||
      parameter.initial < parameter.min ||
      parameter.initial > parameter.max ||
      parameter.smoothing < 0
    ) {
      throw new Error('Invalid parameter range')
    }
  }

  const ids = new Set(model.layers.map(layer => layer.id))

  if (ids.size !== model.layers.length) {
    throw new Error('Duplicate layer id')
  }

  for (const layer of model.layers) {
    const count = layer.vertices.length / 2

    if (
      !textures.has(layer.texture) ||
      count < 3 ||
      count > 65536 ||
      !Number.isInteger(count) ||
      layer.uvs.length !== layer.vertices.length ||
      ![...layer.vertices, ...layer.uvs, layer.opacity, layer.order].every(Number.isFinite) ||
      layer.triangles.length % 3 !== 0 ||
      layer.triangles.some(i => !Number.isInteger(i) || i < 0 || i >= count)
    ) {
      throw new Error(`Invalid mesh: ${layer.id}`)
    }

    for (const [id, keys] of Object.entries(layer.shapes)) {
      if (
        !model.parameters[id] ||
        !keys.length ||
        keys.some(
          (key, i) =>
            ![key.value, key.opacityDelta ?? 0, ...key.offsets].every(Number.isFinite) ||
            key.offsets.length !== layer.vertices.length ||
            (i > 0 && key.value <= keys[i - 1]!.value)
        )
      ) {
        throw new Error(`Invalid shape keys: ${layer.id}/${id}`)
      }
    }
  }
}
