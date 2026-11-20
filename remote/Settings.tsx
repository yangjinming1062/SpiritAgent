import type { ReactElement } from 'react'
import { useState } from 'react'

import type { RemoteApi } from './api'
import { useTask } from './tasks'
import { readTheme, saveTheme, type ThemeChoice } from './theme'
import type { RemoteSession } from './types'
import { Notice, PageHeader } from './ui'

export function Settings({
  api,
  session,
  onLogout
}: {
  api: RemoteApi
  session: RemoteSession
  onLogout: () => void
}): ReactElement {
  const task = useTask()
  const [theme, setTheme] = useState<ThemeChoice>(readTheme)

  return (
    <div className="page">
      <PageHeader subtitle="账户与这台手机的连接" title="设置" />
      <section className="profile-card">
        <div className="avatar">{session.user.username.slice(0, 1)}</div>
        <h2>{session.user.username}</h2>
        <span className="pill">手机已连接</span>
      </section>
      <section className="card">
        <h3>手机连接</h3>
        <dl>
          <div>
            <dt>设备</dt>
            <dd>{session.device.name}</dd>
          </div>
          <div>
            <dt>有效期</dt>
            <dd>{new Date(session.device.expires_at).toLocaleDateString('zh-CN')}</dd>
          </div>
        </dl>
        <p className="muted">陪伴和声音偏好可以在电脑的设置中调整。这里退出只断开这台手机。</p>
      </section>
      <section className="card">
        <h3>界面主题</h3>
        <p className="muted">只影响这台手机上的页面显示。</p>
        <div aria-label="界面主题" className="section-tabs">
          {(
            [
              { id: 'system', label: '跟随系统' },
              { id: 'light', label: '日色' },
              { id: 'dark', label: '夜色' }
            ] as const
          ).map(item => (
            <button
              aria-pressed={theme === item.id}
              className={theme === item.id ? 'selected' : ''}
              key={item.id}
              onClick={() => {
                saveTheme(item.id)
                setTheme(item.id)
              }}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </div>
      </section>
      {task.error ? <Notice>{task.error}</Notice> : null}
      <button
        className="danger full"
        disabled={task.busy}
        onClick={() =>
          task.run(async () => {
            await api.invoke('remote.logout_remote_session')
            onLogout()
          })
        }
        type="button"
      >
        退出手机登录
      </button>
    </div>
  )
}
