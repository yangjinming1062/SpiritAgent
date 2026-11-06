import { createMediaObjectUrl } from '@/features/media'
import { RENDERER_TIMINGS } from '@/shared/constants'
import { $dayPhase, ambientPhaseFilter } from '@/shared/lib/day-phase'
import { backgroundMedia } from '@/shared/platform/desktop'
import { DEFAULT_STAGE_LAYOUT } from '@ipc/contracts'
import type {
  DesktopBackground,
  DesktopBackgroundPlayback,
  DesktopStageLayer,
  DesktopStageLayout
} from '@ipc/contracts'

import { createActorLayer } from './actor-layer'
import {
  acknowledgeSlot,
  applyLayerOrder,
  attachSlotListeners,
  createVideoSlots,
  layerZIndex,
  resetSlot,
  updateMediaLayout,
  updateVideoLayout,
  type VideoSlot,
  waitForFrame
} from './dual-buffer'
import { createReceiptLog } from './receipt'
import { prepareStaticPoster } from './static-poster'
import { createTransitionLayer } from './transition'

const FALLBACK_BACKGROUND = 'radial-gradient(circle at center, var(--ui-fill-hover), var(--ui-app-bg) 72%)'

export interface DesktopPlayerElements {
  first: HTMLVideoElement
  second: HTMLVideoElement
  backdrop: HTMLDivElement
  poster: HTMLImageElement
  transitionFrame: HTMLCanvasElement
  actorFirst: HTMLVideoElement
  actorSecond: HTMLVideoElement
  actorPoster: HTMLImageElement
  actorRoot: HTMLDivElement
}

/**
 * 桌面壁纸播放器：首帧就绪才切换、失败保留当前画面、临时地址随槽位释放。
 * 只消费主进程取得的账户媒体字节，没有鉴权、API 或会话能力。
 */
export function createDesktopPlayer(elements: DesktopPlayerElements): () => void {
  const bridge = backgroundMedia()

  if (!bridge) {
    throw new Error('Desktop background bridge is unavailable')
  }

  const {
    first,
    second,
    backdrop: fill,
    poster: still,
    transitionFrame: transitionCanvas,
    actorFirst,
    actorSecond,
    actorPoster: actorStill,
    actorRoot: actorContainer
  } = elements

  const slots = createVideoSlots([first, second])

  let active: number | null = null
  let retainedLoop: number | null = null
  let pending: number | null = null
  let returningLoop: number | null = null
  let returningFrom: number | null = null
  let notifyReturnFrame = false
  let deferredMedia: DesktopBackground | null = null
  let accountEpoch = -1
  let revision = -1
  let paused = false
  let fallbackPoster: string | null = null
  let fillPosterUrl: string | null = null
  let disposed = false
  let imageGeneration = 0
  let staticMedia: DesktopBackground | null = null
  let waitingImage: DesktopBackground | null = null
  let actorLayout: DesktopStageLayout = DEFAULT_STAGE_LAYOUT

  const receipts = createReceiptLog(bridge, () => disposed)

  const filterFor = (media: DesktopBackground | null): string =>
    ambientPhaseFilter(Boolean(media && !media.reduceMotion), media?.backgroundKind !== 'scene' || media.phaseMedia)

  const transition = createTransitionLayer(transitionCanvas, filterFor)

  const applyOrder = (layout: DesktopStageLayout): void =>
    applyLayerOrder(layout, {
      actorContainer,
      actorPoster: actorStill,
      scenePoster: still,
      transition: transitionCanvas
    })

  const layerZ = (layer: DesktopStageLayer): string => layerZIndex(actorLayout, layer)

  const actor = createActorLayer({
    first: actorFirst,
    second: actorSecond,
    poster: actorStill,
    container: actorContainer,
    receipts,
    isDisposed: () => disposed,
    isPaused: () => paused,
    applyLayerOrder: applyOrder,
    onPresented: () => flushWaitingImage(),
    onPosterReady: () => flushWaitingImage(),
    onDiscardWaiting: () => discardWaitingImage()
  })

  function setFill(url: string | null): void {
    fillPosterUrl = url
    fill.style.backgroundImage = url ? `url("${url}")` : FALLBACK_BACKGROUND
  }

  function syncPhaseFilter(): void {
    for (const slot of slots) {
      slot.element.style.filter = filterFor(slot.media)
    }

    const filter = filterFor(active === null ? staticMedia : slots[active].media)
    fill.style.filter = `blur(24px) brightness(.6) ${filter === 'none' ? '' : filter}`
    still.style.filter = filter
  }

  function updatePosterLayout(): void {
    updateMediaLayout(still, still.naturalWidth, still.naturalHeight, staticMedia?.layout ?? actorLayout)
  }

  function showPoster(url: string | null): void {
    syncPhaseFilter()

    if (url) {
      setFill(url)
    } else if (!fillPosterUrl) {
      setFill(null)
    }

    if (url && active === null) {
      still.src = url
      updatePosterLayout()
      still.style.opacity = '1'
    } else {
      still.style.opacity = '0'
    }
  }

  function acknowledgeImage(
    media: DesktopBackground,
    status: DesktopBackgroundPlayback['status'],
    error?: string
  ): void {
    if (
      status === 'started' &&
      (media.actor || (media.backgroundKind === 'scene' && media.outfit)) &&
      !actor.isVisible() &&
      !actor.hasPoster()
    ) {
      waitingImage = media

      return
    }

    if (!media.playId || receipts.has(media.playId)) {
      return
    }

    receipts.send(media, status, error, 'Image acknowledgment failed')
  }

  function flushWaitingImage(): void {
    const media = waitingImage

    if (!media) {
      return
    }

    waitingImage = null
    acknowledgeImage(media, 'started')
  }

  function discardWaitingImage(): void {
    const media = waitingImage

    if (!media) {
      return
    }

    waitingImage = null
    acknowledgeImage(media, 'failed', 'Actor layer removed before display')
  }

  function interruptDeferred(): void {
    const media = deferredMedia

    if (!media?.playId || disposed) {
      return
    }

    receipts.send(media, 'interrupted')
  }

  function reset(slot: VideoSlot): void {
    resetSlot({
      slot,
      slots,
      isPending: () => pending !== null && slots[pending] === slot,
      onPendingCleared: () => {
        pending = null

        if (active !== null && slots[active].media?.kind === 'loop') {
          slots[active].element.loop = slots[active].media?.transition !== 'fade'
        }
      },
      currentFillUrl: () => fillPosterUrl,
      setFill,
      posterFallback: () => fallbackPoster
    })
  }

  function start(slot: VideoSlot): void {
    if (paused || !slot.ready || slot.terminal) {
      slot.element.pause()

      return
    }

    if (!slot.started && slot.media?.expiresAt && Date.parse(slot.media.expiresAt) <= Date.now()) {
      acknowledgeSlot(slot, 'failed', 'Playback request expired before playback started', receipts)

      return
    }

    const generation = slot.generation

    void slot.element.play().catch(error => {
      if (!paused && slot.generation === generation && slot.ready && !slot.terminal) {
        acknowledgeSlot(slot, 'failed', String(error), receipts)
      }
    })
  }

  function captureSlot(slot: VideoSlot): void {
    if (paused || !slot.ready || !slot.element.videoWidth) {
      return
    }

    transition.capture(slot.element, slot.media)
  }

  function show(index: number): void {
    const slot = slots[index]
    const previous = active === null ? null : slots[active]

    if (slot.media?.kind === 'loop' && staticMedia) {
      acknowledgeImage(staticMedia, 'interrupted')
      staticMedia = null
    }

    if (
      previous &&
      previous !== slot &&
      (previous.media?.transition === 'fade' ||
        slot.media?.transition === 'fade' ||
        !(sharesBase(previous.media, slot.media!) || sharesBase(slot.media, previous.media!)))
    ) {
      captureSlot(previous)
      transition.fade()
    }

    active = index
    actor.allow(slot.media?.backgroundKind === 'scene' && Boolean(slot.media.actor || slot.media.outfit))
    slot.element.style.opacity = '1'
    slot.element.style.zIndex = layerZ(slot.media?.backgroundKind ?? 'scene')
    still.style.opacity = '0'
    showPoster(slot.posterUrl)

    for (let i = 0; i < slots.length; i += 1) {
      if (i !== index) {
        slots[i].element.style.opacity = '0'
        slots[i].element.style.zIndex = '0'
        slots[i].element.pause()
      }
    }

    start(slot)
  }

  function finishReturn(index: number): void {
    const slot = slots[index]

    if (returningLoop !== index || !slot.ready || slot.terminal) {
      return
    }

    const previous = returningFrom
    returningLoop = null
    returningFrom = null

    if (notifyReturnFrame) {
      acknowledgeSlot(slot, 'first-frame', undefined, receipts)
      notifyReturnFrame = false
    }

    show(index)

    if (previous !== null && previous !== index) {
      acknowledgeSlot(slots[previous], 'interrupted', undefined, receipts)
      reset(slots[previous])
    }

    const next = deferredMedia
    deferredMedia = null

    if (next) {
      receive(next)
    }
  }

  function restoreLoop(notifyFirstFrame = false): void {
    if (retainedLoop === null && fallbackPoster && staticMedia) {
      if (active !== null) {
        captureSlot(slots[active])
        reset(slots[active])
      }

      active = null
      showPoster(fallbackPoster)
      transition.fade()

      return
    }

    if (retainedLoop === null || !slots[retainedLoop].ready || slots[retainedLoop].terminal) {
      return
    }

    const index = retainedLoop
    const slot = slots[index]
    returningLoop = index
    returningFrom = active
    notifyReturnFrame = notifyFirstFrame
    slot.element.pause()
    slot.element.loop = slot.media?.transition !== 'fade'

    if (slot.element.currentTime === 0 && !slot.element.seeking) {
      finishReturn(index)
    } else {
      // 寻帧前登记首帧回调；seeked 触发时该帧可能已呈现，暂停状态不会再产生下一帧。
      waitForFrame(
        slot,
        () => disposed,
        () => finishReturn(index)
      )
      slot.element.currentTime = 0
    }
  }

  function sharesBase(loop: DesktopBackground | null, candidate: DesktopBackground): boolean {
    return (
      loop?.kind === 'loop' &&
      loop.presentationEpoch === candidate.presentationEpoch &&
      loop.transition === 'natural' &&
      candidate.transition === 'natural' &&
      loop.continuity === 'base' &&
      candidate.continuity === 'base' &&
      loop.inputImageHash !== null &&
      loop.inputImageHash === candidate.inputImageHash
    )
  }

  function activate(index: number): void {
    const slot = slots[index]

    if (!slot.media || !slot.ready || slot.terminal) {
      return
    }

    if (slot.media.expiresAt && Date.parse(slot.media.expiresAt) <= Date.now()) {
      acknowledgeSlot(slot, 'failed', 'Playback request expired before playback started', receipts)
      reset(slot)

      if (active !== null) {
        slots[active].element.loop = slots[active].media?.kind === 'loop' && slots[active].media?.transition !== 'fade'
        start(slots[active])
      }

      return
    }

    if (slot.timeout) {
      clearTimeout(slot.timeout)
      slot.timeout = null
    }

    pending = null
    const previous = active

    if (
      slot.media.kind === 'once' &&
      previous !== null &&
      slots[previous].media?.kind === 'loop' &&
      slots[previous].media?.presentationEpoch === slot.media.presentationEpoch
    ) {
      retainedLoop = previous
    } else {
      retainedLoop = null
    }

    show(index)

    if (previous !== null && previous !== index && previous !== retainedLoop) {
      acknowledgeSlot(slots[previous], 'interrupted', undefined, receipts)
      reset(slots[previous])
    }
  }

  function onLoaded(index: number): void {
    const slot = slots[index]

    if (!slot.media || slot.ready || slot.terminal) {
      return
    }

    if (slot.media.expiresAt && Date.parse(slot.media.expiresAt) <= Date.now()) {
      acknowledgeSlot(slot, 'failed', 'Playback request expired before the first frame was ready', receipts)
      reset(slot)

      return
    }

    if (slot.timeout) {
      clearTimeout(slot.timeout)
      slot.timeout = null
    }

    slot.ready = true
    updateVideoLayout(slot, actorLayout)
    acknowledgeSlot(slot, 'first-frame', undefined, receipts)

    if (slot.media.kind === 'once' && active !== null && sharesBase(slots[active].media, slot.media)) {
      pending = index
      // 保底循环继续到自然结束，已解码的短动作从共同基础画面开始。
      slots[active].element.loop = false

      if (slot.media.expiresAt) {
        slot.timeout = setTimeout(
          () => {
            if (pending === index) {
              acknowledgeSlot(slot, 'failed', 'Playback request expired while waiting for the loop boundary', receipts)
              reset(slot)
            }
          },
          Math.max(0, Date.parse(slot.media.expiresAt) - Date.now())
        )
      }

      return
    }

    activate(index)
  }

  const listeners = slots.map((slot, index) =>
    attachSlotListeners(slot, {
      isDisposed: () => disposed,
      loaded: () => onLoaded(index),
      playing: () => {
        if (active === index && !slot.started && !slot.terminal) {
          slot.started = true
          acknowledgeSlot(slot, 'started', undefined, receipts)
        }
      },
      resize: () => updateVideoLayout(slot, actorLayout),
      ended: () => {
        if (active !== index || !slot.element.ended) {
          return
        }

        if (slot.media?.kind === 'loop' && pending !== null) {
          activate(pending)
        } else if (slot.media?.kind === 'loop' && slot.media.transition === 'fade') {
          captureSlot(slot)
          waitForFrame(slot, () => disposed, transition.fade)
          slot.element.currentTime = 0
          start(slot)
        } else if (slot.media?.kind === 'once') {
          // 终态交给宿主重读最新基础舞台，不能直接恢复演出开始时保留的旧循环。
          acknowledgeSlot(slot, 'completed', undefined, receipts)
        }
      },
      failed: () => {
        if (slot.media && !slot.terminal && slot.element.error) {
          acknowledgeSlot(slot, 'failed', slot.element.error?.message || 'Video could not be decoded', receipts)

          if (active !== index) {
            reset(slot)
          }
        }
      }
    })
  )

  function clear(): void {
    imageGeneration += 1

    if (staticMedia) {
      acknowledgeImage(staticMedia, 'interrupted')
      staticMedia = null
    }

    actor.reset()
    actorLayout = DEFAULT_STAGE_LAYOUT
    returningLoop = null
    returningFrom = null
    notifyReturnFrame = false
    transition.reset()
    interruptDeferred()
    deferredMedia = null

    for (const slot of slots) {
      acknowledgeSlot(slot, 'interrupted', undefined, receipts)
      reset(slot)
    }

    active = null
    retainedLoop = null
    pending = null
    receipts.clear()

    if (fallbackPoster) {
      URL.revokeObjectURL(fallbackPoster)
      fallbackPoster = null
    }

    setFill(null)
    still.removeAttribute('src')
    showPoster(null)
  }

  function receive(request: DesktopBackground): void {
    const media: DesktopBackground =
      request.playTarget === 'actor' ? { ...request, playId: null, kind: 'loop', expiresAt: null } : request

    if (media.accountEpoch < accountEpoch || (media.accountEpoch === accountEpoch && media.revision < revision)) {
      return
    }

    if (media.accountEpoch !== accountEpoch || media.clear) {
      clear()
      accountEpoch = media.accountEpoch
    }

    revision = media.revision
    actorLayout = media.layout
    applyOrder(actorLayout)
    const nextImageGeneration = ++imageGeneration
    paused = media.paused || media.reduceMotion
    document.documentElement.dataset.theme = media.theme
    document.documentElement.dataset.palette = media.theme.startsWith('night') ? 'night' : 'day'

    if (media.clear) {
      requestAnimationFrame(() => {
        if (!disposed && nextImageGeneration === imageGeneration) {
          acknowledgeImage(media, 'started')
        }
      })

      return
    }

    // 主体层与背景层独立接收：环境层换图不打断主体，主体缺失只清自身。
    actor.receive(request)

    for (const slot of slots) {
      if (slot.media?.playId === media.playId && (media.playId !== null || slot.media.revision === media.revision)) {
        slot.media = {
          ...slot.media,
          revision: media.revision,
          geometryMode: media.geometryMode,
          paused,
          reduceMotion: media.reduceMotion
        }
        slot.media.backgroundKind = media.backgroundKind
        slot.media.phaseMedia = media.phaseMedia
        slot.element.style.zIndex = layerZ(slot.media.backgroundKind)
        updateVideoLayout(slot, actorLayout)
      }
    }

    syncPhaseFilter()

    const existing = slots.findIndex(
      slot =>
        slot.media &&
        slot.media.playId === media.playId &&
        (media.playId !== null || slot.media.revision === media.revision)
    )

    if (existing !== -1 || (media.playId && receipts.has(media.playId))) {
      if (existing !== -1 && active === null) {
        showPoster(slots[existing].posterUrl)
      }

      if (active !== null) {
        start(slots[active])
      }

      return
    }

    if (returningLoop !== null) {
      if (deferredMedia?.playId && deferredMedia.playId !== media.playId) {
        interruptDeferred()
      }

      deferredMedia = media

      return
    }

    const reusableLoop = slots.findIndex(
      slot =>
        slot.media?.kind === 'loop' &&
        media.kind === 'loop' &&
        slot.ready &&
        !slot.terminal &&
        slot.media.visualId === media.visualId &&
        slot.media.presentationEpoch === media.presentationEpoch &&
        slot.media.visualVersion === media.visualVersion &&
        slot.media.continuity === media.continuity &&
        slot.media.transition === media.transition &&
        slot.media.geometryMode === media.geometryMode &&
        slot.media.inputImageHash === media.inputImageHash
    )

    if (media.video && reusableLoop !== -1) {
      if (pending !== null) {
        acknowledgeSlot(slots[pending], 'interrupted', undefined, receipts)
        reset(slots[pending])
      }

      const slot = slots[reusableLoop]
      acknowledgeSlot(slot, 'interrupted', undefined, receipts)
      slot.media = media
      slot.started = false
      slot.terminal = false

      if (active === reusableLoop && media.playId === null) {
        retainedLoop = null
        start(slot)

        return
      }

      retainedLoop = reusableLoop
      restoreLoop(true)

      return
    }

    if (!media.video) {
      if (!media.poster) {
        if (media.actor) {
          for (const slot of slots) {
            reset(slot)
          }

          active = retainedLoop = pending = null
          still.style.opacity = '0'
          setFill(null)
          actor.allow(true)
        }

        return
      }

      if (
        staticMedia?.playId === media.playId &&
        staticMedia?.visualId === media.visualId &&
        staticMedia?.visualVersion === media.visualVersion
      ) {
        return
      }

      let preparedPoster: string | null = null
      void prepareStaticPoster(media.poster, media.backgroundKind === 'scene' ? null : media.outfit)
        .then(nextPoster => {
          preparedPoster = nextPoster

          if (disposed || nextImageGeneration !== imageGeneration) {
            URL.revokeObjectURL(nextPoster)

            return
          }

          if (media.expiresAt && Date.parse(media.expiresAt) <= Date.now()) {
            acknowledgeImage(media, 'failed', 'Image request expired before display')
            URL.revokeObjectURL(nextPoster)

            return
          }

          if (active !== null) {
            captureSlot(slots[active])
          }

          if (staticMedia) {
            acknowledgeImage(staticMedia, 'interrupted')
          }

          for (const slot of slots) {
            acknowledgeSlot(slot, 'interrupted', undefined, receipts)
            reset(slot)
          }

          active = null
          retainedLoop = null
          const previousPoster = fallbackPoster
          fallbackPoster = nextPoster
          staticMedia = media
          actor.allow(media.backgroundKind === 'scene' && Boolean(media.actor || media.outfit))
          showPoster(nextPoster)
          transition.fade()
          requestAnimationFrame(() => {
            if (!disposed && nextImageGeneration === imageGeneration) {
              acknowledgeImage(media, 'started')
            }

            if (previousPoster) {
              URL.revokeObjectURL(previousPoster)
            }
          })
        })
        .catch(error => {
          if (preparedPoster) {
            URL.revokeObjectURL(preparedPoster)
          }

          if (!disposed && nextImageGeneration === imageGeneration) {
            acknowledgeImage(media, 'failed', String(error))
          }
        })

      return
    }

    // 新套装或持续画面替换不能返回已失效的保留循环。
    if (retainedLoop !== null && media.kind === 'loop') {
      const retained = slots[retainedLoop]

      if (
        retained.media?.visualId !== media.visualId ||
        retained.media?.presentationEpoch !== media.presentationEpoch ||
        retained.media?.visualVersion !== media.visualVersion
      ) {
        acknowledgeSlot(retained, 'interrupted', undefined, receipts)
        reset(retained)
        retainedLoop = null
      }
    }

    // 新动作到达时先回到保留的持续状态，另一缓冲才可安全解码。
    if (active !== null && slots[active].media?.kind === 'once' && retainedLoop !== null) {
      acknowledgeSlot(slots[active], 'interrupted', undefined, receipts)
      restoreLoop()

      if (returningLoop !== null) {
        deferredMedia = media

        return
      }
    }

    const next = active === 0 ? 1 : 0
    const slot = slots[next]

    if (slot.media) {
      acknowledgeSlot(slot, 'interrupted', undefined, receipts)
    }

    reset(slot)
    slot.media = media
    slot.videoUrl = createMediaObjectUrl(media.video)
    slot.posterUrl = media.poster ? createMediaObjectUrl(media.poster) : null
    slot.element.loop = media.kind === 'loop' && media.transition !== 'fade'
    slot.element.style.objectFit = 'contain'
    slot.element.src = slot.videoUrl

    if (slot.posterUrl) {
      slot.element.poster = slot.posterUrl

      if (active === null && !staticMedia) {
        showPoster(slot.posterUrl)
      }
    }

    slot.timeout = setTimeout(() => {
      slot.timeout = null

      if (!slot.ready) {
        acknowledgeSlot(slot, 'failed', 'Video first frame timed out', receipts)
        reset(slot)
      }
    }, RENDERER_TIMINGS.desktopBackgroundMediaTimeoutMs)
    slot.element.load()
    waitForFrame(
      slot,
      () => disposed,
      () => onLoaded(next)
    )

    if (active !== null && paused) {
      slots[active].element.pause()
    }
  }

  const updateAllMediaLayouts = (): void => {
    slots.forEach(slot => updateVideoLayout(slot, actorLayout))
    updatePosterLayout()
    actor.layout()
  }

  const off = bridge.onMedia(receive)
  const offPhase = $dayPhase.listen(syncPhaseFilter)

  window.addEventListener('resize', updateAllMediaLayouts)
  still.addEventListener('load', updatePosterLayout)

  void bridge.ready().catch(error => console.warn('Desktop background readiness failed', error))

  return () => {
    off()
    offPhase()
    clear()
    disposed = true
    actor.dispose()
    listeners.forEach(remove => remove())
    window.removeEventListener('resize', updateAllMediaLayouts)
    still.removeEventListener('load', updatePosterLayout)
  }
}
