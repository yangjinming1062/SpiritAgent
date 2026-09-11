import * as THREE from 'three'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import { PMREMGenerator, WebGPURenderer } from 'three/webgpu'

/** Three-point lighting + PMREM environment for PBR material reflections.
 * Tuned for a realistic character bust/half-body framing. */

type RendererHost = THREE.WebGLRenderer | WebGPURenderer

export class LightingRig {
  private readonly ambient: THREE.AmbientLight
  private readonly key: THREE.DirectionalLight
  private readonly fill: THREE.DirectionalLight
  private readonly rim: THREE.DirectionalLight

  private readonly eyeLight: THREE.DirectionalLight

  // env 贴图是构造后唯一存活的部分；经典 PMREMGenerator
  // 绑定在 WebGLRenderer 内部，webgpu 版则绑定在 WebGPU 后端，
  // 因此 target 句柄因渲染器类型而异。
  private readonly envTexture: THREE.Texture
  private readonly disposeEnvTarget: () => void

  constructor(scene: THREE.Scene, renderer: RendererHost, enableShadows: boolean) {
    // PMREM 环境贴图为 PBR 材质提供真实的环境反射，
    // 无需额外的 HDRI 文件。
    if (renderer instanceof WebGPURenderer) {
      const pmrem = new PMREMGenerator(renderer)
      const target = pmrem.fromScene(new RoomEnvironment(), 0.04)
      this.envTexture = target.texture
      this.disposeEnvTarget = () => target.dispose()
      pmrem.dispose()
    } else {
      const pmrem = new THREE.PMREMGenerator(renderer)
      const target = pmrem.fromScene(new RoomEnvironment(), 0.04)
      this.envTexture = target.texture
      this.disposeEnvTarget = () => target.dispose()
      pmrem.dispose()
    }

    scene.environment = this.envTexture

    this.ambient = new THREE.AmbientLight(0xffffff, 0.3)
    scene.add(this.ambient)

    // 主光——暖色，左前方。300×360 桌面伙伴窗口默认关闭（shadow map 是单笔最大的 GPU 开销）；开启时使用 1024² PCF radius 1。
    this.key = new THREE.DirectionalLight(0xfff6ee, 2.2)
    this.key.position.set(1.4, 2.2, 3.2)

    if (enableShadows) {
      this.key.castShadow = true
      this.key.shadow.mapSize.set(1024, 1024)
      this.key.shadow.camera.near = 0.5
      this.key.shadow.camera.far = 12
      this.key.shadow.camera.left = -2.5
      this.key.shadow.camera.right = 2.5
      this.key.shadow.camera.top = 2.5
      this.key.shadow.camera.bottom = -1.5
      this.key.shadow.bias = -0.0005
      this.key.shadow.radius = 1
    } else {
      this.key.castShadow = false
    }

    scene.add(this.key)

    // 补光——冷色，右前方，更柔和；用于提升阴影细节。
    this.fill = new THREE.DirectionalLight(0xdbe8ff, 0.85)
    this.fill.position.set(-1.6, 1.4, 2.6)
    scene.add(this.fill)

    // 眼神高光 / 面部柔光——正前方平视高度，让眼神光更灵动、面部补光更柔和。
    this.eyeLight = new THREE.DirectionalLight(0xffffff, 0.8)
    this.eyeLight.position.set(0, 1.45, 2.5)
    this.eyeLight.target.position.set(0, 1.45, 0)
    scene.add(this.eyeLight)
    scene.add(this.eyeLight.target)

    // 轮廓光——后上方，制造角色与背景的边缘分离。
    this.rim = new THREE.DirectionalLight(0xede6ff, 1.1)
    this.rim.position.set(0.4, 2.6, -3.0)
    scene.add(this.rim)
  }

  dispose(scene: THREE.Scene): void {
    scene.environment = null
    this.disposeEnvTarget()
    scene.remove(this.ambient, this.key, this.fill, this.eyeLight, this.eyeLight.target, this.rim)
  }
}
