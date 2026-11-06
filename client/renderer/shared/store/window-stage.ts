import { atom } from 'nanostores'

/** 环境层目标地址：由窗口呈现装配按当前投影写入，舞台组件只读。 */
export const $windowScenePath = atom<string | null>(null)
/** 窗口实际已呈现的环境地址；由舞台组件在首帧可见后写入，装配据此确认静态投影。 */
export const $windowScenePresented = atom<string | null>(null)
/** 静态投影生效期间舞台只显示冻结环境与造型，不推进动态媒体。 */
export const $windowStaticProjection = atom(false)
