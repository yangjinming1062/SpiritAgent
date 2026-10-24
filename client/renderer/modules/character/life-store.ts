import { atom } from 'nanostores'

/** 后端报告生活状态变化时递增，资料页据此重新读取权威状态。 */
export const $lifeRevision = atom(0)

export function handleLifeUpdated(): void {
  $lifeRevision.set($lifeRevision.get() + 1)
}
