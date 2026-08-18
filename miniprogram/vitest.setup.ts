import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// 显式清理（WHY：vitest 未开 globals 时 RTL 的 auto-cleanup 检测不到全局 afterEach，DOM 会跨用例累积）
afterEach(() => {
  cleanup()
})
