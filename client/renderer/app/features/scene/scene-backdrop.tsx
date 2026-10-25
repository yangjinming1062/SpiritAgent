// 环境与立绘分别确认加载，只有当前可见的冻结来源才参与呈现回执。
import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import {
  $windowScenePresented,
  $windowVisualProjection,
  useVisualProjection
} from '@/app/workflows/visual-presentation'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { BTN_SUBTLE } from '@/shared/panel'
import { backendApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import styles from './scene-backdrop.module.css'

export function SceneBackdrop(): React.JSX.Element {
  useVisualProjection()
  const projection = useStore($windowVisualProjection)
  const auth = useStore($auth)
  const owner = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const path = projection?.scene_path ?? null
  const [image, setImage] = useState<{ owner: string; path: string; url: string } | null>(null)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const imageRef = useRef<HTMLImageElement>(null)
  const strings = useStrings()
  const shown = image?.owner === owner ? image : null

  useEffect(() => {
    const scope = captureAuthScope()
    let current = true
    let candidate: HTMLImageElement | null = null
    let timeout: ReturnType<typeof setTimeout> | undefined
    setFailed(false)

    if (!owner || !path || !scope) {
      setImage(null)

      return () => {
        current = false
      }
    }

    void backendApi()
      .apiAsset({ url: path, preferCache: true })
      .then(async url => {
        if (!current || !scope()) {
          return
        }

        if (!url) {
          throw new Error('Frozen scene is unavailable')
        }

        candidate = new Image()
        candidate.src = url
        const decoding = candidate.decode()
        await new Promise<void>((resolve, reject) => {
          timeout = setTimeout(() => reject(new Error('Frozen scene decode timed out')), 15000)
          void decoding.then(resolve, reject)
        }).finally(() => clearTimeout(timeout))

        if (current && scope()) {
          setImage({ owner, path, url })
        }
      })
      .catch(error => {
        if (current && scope()) {
          setFailed(true)
          log.warn('scene-backdrop', 'Frozen scene unavailable', error)
        }
      })

    return () => {
      current = false
      clearTimeout(timeout)

      if (candidate) {
        candidate.src = ''
      }
    }
  }, [owner, path, retry])

  useEffect(() => {
    if (shown && imageRef.current?.complete && imageRef.current.naturalWidth > 0) {
      $windowScenePresented.set(shown.path)
    }

    return () => {
      if ($windowScenePresented.get() === shown?.path) {
        $windowScenePresented.set(null)
      }
    }
  }, [shown])

  return (
    <div className={styles.root}>
      {shown && (
        <img
          alt=""
          className={styles.image}
          onError={() => {
            $windowScenePresented.set(null)
            setFailed(true)
          }}
          onLoad={() => $windowScenePresented.set(shown.path)}
          ref={imageRef}
          src={shown.url}
        />
      )}
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
