import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import {
  $windowScenePresented,
  $windowStaticProjection,
  $windowVisualProjection,
  useVisualProjection
} from '@/app/workflows/visual-presentation'
import { isCharacterMediaVisible, observeCharacterStageVisibility } from '@/modules/character'
import { $stagePlan, type StageScenePlan } from '@/modules/visuals'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { acquireStageVideo } from '@/shared/lib/stage-video-cache'
import { createStageVideoLayer } from '@/shared/lib/stage-video-layer'
import { BTN_SUBTLE } from '@/shared/panel'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'
import { useStrings } from '@/shared/strings'

import styles from './scene-backdrop.module.css'

export function SceneBackdrop(): React.JSX.Element {
  useVisualProjection()
  const plan = useStore($stagePlan)
  const projection = useStore($windowVisualProjection)
  const staticProjection = useStore($windowStaticProjection)
  const auth = useStore($auth)
  const owner = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const scene = staticProjection ? null : plan.scene
  const path = (scene?.isVideo ? scene.posterUrl : scene?.url) ?? projection?.scene_path ?? null
  const [image, setImage] = useState<{ owner: string; path: string; url: string } | null>(null)
  const [video, setVideo] = useState<StageScenePlan | null>(null)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const first = useRef<HTMLVideoElement>(null)
  const second = useRef<HTMLVideoElement>(null)
  const imageRef = useRef<HTMLImageElement>(null)
  const layer = useRef<ReturnType<typeof createStageVideoLayer<StageScenePlan>> | null>(null)
  const strings = useStrings()
  const shown = image?.owner === owner ? image : null

  useEffect(() => {
    if (!first.current || !second.current) {
      return
    }

    const player = createStageVideoLayer<StageScenePlan>([first.current, second.current], {
      presented: source => setVideo(source?.value ?? null),
      started: () => undefined,
      ended: () => undefined,
      failed: (_source, error) => log.warn('scene-backdrop', 'Scene video failed', error)
    })

    layer.current = player
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')

    const sync = (): void =>
      player.setPaused(!isCharacterMediaVisible() || query.matches || $presentation.get().fullscreen)

    const stop = observeCharacterStageVisibility(sync)
    query.addEventListener('change', sync)

    return () => {
      stop()
      query.removeEventListener('change', sync)
      player.dispose()
      layer.current = null
    }
  }, [])

  useEffect(() => {
    layer.current?.set(
      owner && scene?.isVideo
        ? {
            key: `${owner}:${scene.visualId}:${scene.version}`,
            loop: true,
            value: scene,
            load: () => acquireStageVideo(scene.url)
          }
        : null
    )
  }, [owner, scene])

  useEffect(() => {
    const scope = captureAuthScope()
    let current = true
    let timeout: ReturnType<typeof setTimeout> | undefined
    setFailed(false)

    if (!owner || !path || !scope) {
      setImage(null)

      return
    }

    const candidate = new Image()
    const request = backendApi().apiAsset({ url: path, preferCache: true })
    void new Promise<string>((resolve, reject) => {
      timeout = setTimeout(() => reject(new Error('Scene image timed out')), 15000)
      void request
        .then(async url => {
          if (!url) {
            throw new Error('Scene image unavailable')
          }

          candidate.src = url
          await candidate.decode()
          resolve(url)
        })
        .catch(reject)
    })
      .then(url => {
        if (current && scope()) {
          setImage({ owner, path, url })
        }
      })
      .catch(error => {
        if (current && scope()) {
          setFailed(true)
          log.warn('scene-backdrop', 'Scene image failed', error)
        }
      })
      .finally(() => clearTimeout(timeout))

    return () => {
      current = false
      clearTimeout(timeout)
      candidate.src = ''
    }
  }, [owner, path, retry])

  useEffect(() => {
    const videoMatches = video && video.visualId === scene?.visualId && video.version === scene.version
    const presented = videoMatches ? (projection?.scene_path ?? null) : (shown?.path ?? null)

    if (presented && (video || (imageRef.current?.complete && imageRef.current.naturalWidth > 0))) {
      $windowScenePresented.set(presented)
    }

    return () => {
      if ($windowScenePresented.get() === presented) {
        $windowScenePresented.set(null)
      }
    }
  }, [shown, video, scene, projection?.scene_path])

  const videoStyle: React.CSSProperties = {
    position: 'absolute',
    inset: 0,
    width: '100%',
    height: '100%',
    objectFit: 'cover',
    opacity: 0
  }

  return (
    <div className={styles.root}>
      {shown && (
        <img
          alt=""
          className={styles.image}
          draggable={false}
          onError={() => {
            $windowScenePresented.set(null)
            setFailed(true)
          }}
          onLoad={() => {
            if (!video) {
              $windowScenePresented.set(shown.path)
            }
          }}
          ref={imageRef}
          src={shown.url}
        />
      )}
      <video muted playsInline preload="auto" ref={first} style={videoStyle} />
      <video muted playsInline preload="auto" ref={second} style={videoStyle} />
      {failed && (
        <div className={styles.error} role="alert">
          <span>{strings.creation.previewFailed}</span>
          <button className={BTN_SUBTLE} onClick={() => setRetry(value => value + 1)} type="button">
            {strings.creation.retryLoad}
          </button>
        </div>
      )}
    </div>
  )
}
