import { PropsWithChildren } from 'react'
import { useLaunch } from '@tarojs/taro'

import './app.scss'
import { ensureLogin } from './utils/auth'

function App({ children }: PropsWithChildren<any>) {
  useLaunch(() => {
    // 静默登录（WHY：登录无感；失败不阻断——核心闯关可游客使用，金币/记录需登录）
    ensureLogin().catch(() => {
      console.warn('静默登录失败，本次会话以游客模式运行')
    })
  })

  // children 是将要会渲染的页面
  return children
}

export default App
