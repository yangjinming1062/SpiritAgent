import type { WebContents } from 'electron'

import type {
  DesktopBackground,
  DesktopBackgroundPlayback,
  DesktopBackgroundRequest,
  DesktopMediaBytes,
  DesktopMediaReference
} from '@ipc/contracts'
import { errorMessage } from '@runtime'

import { emptyDesktopBackground, parseDesktopBackgroundRequest } from './desktop-background'

interface DesktopBackgroundOptions {
  authIdentity: () => string | null
  assertDesktop: (sender: WebContents) => void
  captureEligibility: (sender: WebContents) => () => boolean
  isPaused: () => boolean
  seedTheme: () => string | undefined
  loadMedia: (sender: WebContents, reference: DesktopMediaReference) => Promise<DesktopMediaBytes>
  send: (background: DesktopBackground) => void
  playback: (playback: DesktopBackgroundPlayback) => void
  log: (message: string) => void
}

export function createDesktopBackgroundRuntime(options: DesktopBackgroundOptions) {
  let accountEpoch = 0
  let backgroundGeneration = 0
  let backgroundRevision = 0
  let requestedBackground: DesktopBackgroundRequest | null = null
  let loadedBackground: DesktopBackgroundRequest | null = null
  let currentBackground = emptyDesktopBackground(accountEpoch, backgroundRevision)
  const dispatchedPlays = new Map<string, { accountEpoch: number; revision: number }>()

  function backgroundPaused(): boolean {
    return options.isPaused() || requestedBackground?.paused === true || currentBackground.reduceMotion
  }

  function sendBackground(): void {
    options.send(currentBackground)
  }

  function publishBackgroundState(): void {
    const paused = backgroundPaused()

    if (currentBackground.paused !== paused) {
      currentBackground = { ...currentBackground, paused }
      sendBackground()
    }
  }

  function interruptBackground(): void {
    for (const [playId, dispatched] of dispatchedPlays) {
      options.playback({ ...dispatched, playId, status: 'interrupted' })
    }

    dispatchedPlays.clear()
    currentBackground = { ...currentBackground, paused: true }
    sendBackground()
  }

  async function setBackground(sender: WebContents, raw: unknown): Promise<void> {
    options.assertDesktop(sender)
    const request = parseDesktopBackgroundRequest(raw)
    const identity = options.authIdentity()
    const eligible = options.captureEligibility(sender)
    const generation = ++backgroundGeneration

    const valid = (): boolean => generation === backgroundGeneration && eligible()

    if (request.authSessionId !== identity || !valid()) {
      // 换号与代次更替属预期竞态，记日志后仍须失败返回，避免把未应用的背景伪装成成功。
      options.log(`[desktop] dropped stale background request (playId=${String(request.playId)})`)
      throw new Error('桌面背景请求已失效。')
    }

    if (
      request.playId !== currentBackground.playId &&
      request.expiresAt &&
      Date.parse(request.expiresAt) <= Date.now()
    ) {
      throw new Error('桌面播放请求已过期。')
    }

    if (request.clear) {
      requestedBackground = request
      interruptBackground()
      loadedBackground = null
      currentBackground = {
        ...emptyDesktopBackground(accountEpoch, ++backgroundRevision, request.theme),
        reduceMotion: request.reduceMotion
      }
      sendBackground()

      return
    }

    const sameMedia = (a: DesktopMediaReference | null, b: DesktopMediaReference | null): boolean =>
      a?.url === b?.url && a?.contentHash === b?.contentHash

    const unchanged =
      loadedBackground &&
      request.playId === loadedBackground.playId &&
      request.setEpoch === loadedBackground.setEpoch &&
      request.setId === loadedBackground.setId &&
      request.actionId === loadedBackground.actionId &&
      request.actionVersion === loadedBackground.actionVersion &&
      request.continuity === loadedBackground.continuity &&
      request.baseVersion === loadedBackground.baseVersion &&
      sameMedia(request.video, loadedBackground.video) &&
      sameMedia(request.poster, loadedBackground.poster)

    let video = currentBackground.video
    let poster = currentBackground.poster

    if (!unchanged) {
      try {
        ;[video, poster] = await Promise.all([
          request.video ? options.loadMedia(sender, request.video) : null,
          request.poster ? options.loadMedia(sender, request.poster) : null
        ])

        if (video && !/^video\/(?:mp4|quicktime)(?:;|$)/i.test(video.mime)) {
          throw new Error('桌面视频格式不可用。')
        }

        if (poster && !/^image\/(?:png|jpeg|webp|gif)(?:;|$)/i.test(poster.mime)) {
          throw new Error('桌面封面格式不可用。')
        }
      } catch (error) {
        if (valid() && request.playId) {
          options.playback({
            accountEpoch,
            revision: ++backgroundRevision,
            playId: request.playId,
            status: 'failed',
            error: errorMessage(error).slice(0, 2000)
          })
        }

        throw error
      }
    }

    if (!valid()) {
      options.log(`[desktop] dropped stale background request after load (playId=${String(request.playId)})`)
      throw new Error('桌面背景请求已失效。')
    }

    if (
      request.playId !== currentBackground.playId &&
      request.expiresAt &&
      Date.parse(request.expiresAt) <= Date.now()
    ) {
      throw new Error('桌面播放请求已过期。')
    }

    requestedBackground = request

    const { authSessionId: _authSessionId, video: _video, poster: _poster, ...settings } = request
    currentBackground = {
      ...settings,
      accountEpoch,
      revision: unchanged ? currentBackground.revision : ++backgroundRevision,
      video,
      poster
    }
    currentBackground.paused = backgroundPaused()
    loadedBackground = request

    if (request.playId && video) {
      dispatchedPlays.set(request.playId, { accountEpoch, revision: currentBackground.revision })
    }

    sendBackground()
  }

  function acknowledge(raw: unknown): void {
    const value = raw as Partial<DesktopBackgroundPlayback> | null
    const dispatched = typeof value?.playId === 'string' ? dispatchedPlays.get(value.playId) : null

    if (
      !dispatched ||
      value?.accountEpoch !== accountEpoch ||
      value.revision !== dispatched.revision ||
      !['first-frame', 'started', 'completed', 'interrupted', 'failed'].includes(String(value.status)) ||
      (value.error !== undefined && (typeof value.error !== 'string' || value.error.length > 2000))
    ) {
      return
    }

    options.playback(value as DesktopBackgroundPlayback)

    if (value.status === 'completed' || value.status === 'interrupted' || value.status === 'failed') {
      dispatchedPlays.delete(value.playId!)
    }
  }

  function reset(): void {
    backgroundGeneration += 1
    requestedBackground = null
    loadedBackground = null
    dispatchedPlays.clear()
    currentBackground = emptyDesktopBackground(accountEpoch, ++backgroundRevision, options.seedTheme())
  }

  return {
    getState: () => currentBackground,
    set: setBackground,
    send: sendBackground,
    publish: publishBackgroundState,
    interrupt: interruptBackground,
    acknowledge,
    invalidate: () => {
      backgroundGeneration += 1
    },
    reset,
    accountChanged: () => {
      accountEpoch += 1
      reset()
      sendBackground()
    }
  }
}
