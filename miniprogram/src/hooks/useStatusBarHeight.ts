import { useEffect, useState } from 'react'
import Taro from '@tarojs/taro'

/**
 * 获取系统状态栏高度 px（WHY：自定义导航下页面内容从屏幕顶部开始，
 * CSS env(safe-area-inset-top) 在开发者工具与部分安卓机型不生效，
 * 必须用运行时 API 动态计算，避免内容与时间/胶囊重叠）
 */
export function useStatusBarHeight(): number {
  const [height, setHeight] = useState(0)

  useEffect(() => {
    try {
      const info = Taro.getWindowInfo()
      setHeight(info.statusBarHeight ?? 0)
    } catch {
      // 低版本基础库回退
      try {
        setHeight(Taro.getSystemInfoSync().statusBarHeight ?? 0)
      } catch {
        // 忽略，保持 0（不额外留白）
      }
    }
  }, [])

  return height
}
