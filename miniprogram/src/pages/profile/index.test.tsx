import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Profile from './index'
import { getMe } from '../../api/user'
import type { UserProfile } from '../../api/user'

// 模拟 H5 刷新竞态：useDidShow 的回调被捕获但不自动触发
// （WHY：Taro H5 刷新时 onShow 事件早于组件挂载，监听丢失——正是要修复的 bug）
let showHandler: (() => void) | null = null

vi.mock('@tarojs/taro', () => ({
  default: {
    navigateTo: vi.fn(),
    switchTab: vi.fn(),
    showToast: vi.fn(),
  },
  useDidShow: (fn: () => void) => {
    showHandler = fn
  },
}))

vi.mock('@tarojs/components', () => ({
  // style 序列化为 data-style 属性（WHY：jsdom CSS 解析器拒绝 rpx 单位，内联样式不落 DOM，折线图坐标断言改读 data-style）
  View: (props: React.HTMLAttributes<HTMLDivElement>) => {
    const { style, ...rest } = props
    return <div {...rest} data-style={JSON.stringify(style)} />
  },
  Text: (props: React.HTMLAttributes<HTMLSpanElement>) => <span {...props} />,
  Button: (props: React.HTMLAttributes<HTMLButtonElement>) => <button {...props} />,
  Input: (props: React.HTMLAttributes<HTMLInputElement>) => <input {...props} />,
}))

vi.mock('../../hooks/useStatusBarHeight', () => ({ useStatusBarHeight: () => 0 }))

// 空态数据（sessions=0 走"暂无学习记录"渲染分支，减少 DOM 复杂度）
const EMPTY_PROFILE: UserProfile = {
  user: { id: 1, nickname: '测试', avatar_text: '测', coins: 0 },
  stats: { sessions: 0, correct_rate: 0, knowledge_points: 0, total_correct: 0 },
  daily_answers: [
    { date: '2026-08-12', count: 0 },
    { date: '2026-08-13', count: 0 },
    { date: '2026-08-14', count: 0 },
    { date: '2026-08-15', count: 0 },
    { date: '2026-08-16', count: 0 },
    { date: '2026-08-17', count: 0 },
    { date: '2026-08-18', count: 0 },
  ],
  knowledge_tree: [],
  recent_sessions: [],
}

vi.mock('../../api/user', () => ({
  getMe: vi.fn(),
  updateMe: vi.fn(),
}))

beforeEach(() => {
  showHandler = null
  vi.mocked(getMe).mockReset()
  vi.mocked(getMe).mockResolvedValue(EMPTY_PROFILE)
})

describe('Profile 页数据加载', () => {
  it('首次挂载时加载数据（H5 刷新导致 useDidShow 事件丢失时也能加载）', async () => {
    render(<Profile />)
    await waitFor(() => expect(getMe).toHaveBeenCalled())
  })

  it('页面再次显示时刷新数据（tab 切换场景）', async () => {
    render(<Profile />)
    await waitFor(() => expect(getMe).toHaveBeenCalledTimes(1))
    act(() => showHandler!())
    expect(getMe).toHaveBeenCalledTimes(2)
  })
})

describe('Profile 页近七日折线图', () => {
  it('折线图渲染：7 点 6 线 + 数字常驻 + 今日金色', async () => {
    // stats.sessions 必须 > 0（WHY：sessions=0 走"暂无学习记录"空态分支，图表不渲染）
    const withData = {
      ...EMPTY_PROFILE,
      stats: { ...EMPTY_PROFILE.stats, sessions: 3 },
      daily_answers: [2, 0, 5, 3, 4, 1, 3].map((count, i) => ({ date: `2026-08-${12 + i}`, count })),
    }
    vi.mocked(getMe).mockResolvedValue(withData)
    render(<Profile />)
    await screen.findByText('近七日答题')
    expect(document.querySelectorAll('.pt').length).toBe(7)
    expect(document.querySelectorAll('.seg').length).toBe(6)
    expect(document.querySelectorAll('.digit').length).toBe(7)
    expect(document.querySelector('.pt.today')).not.toBeNull()
    expect(document.querySelector('.digit.today')?.textContent).toBe('3') // 今日=最后一项=3
    // 0 值日也常驻标注（第 2 项 = 2026-08-13 = 0；WHY：stats 卡也有数字 0，必须限定在 .digit 内断言）
    expect(document.querySelectorAll('.digit')[1].textContent).toBe('0')
    // 常量字面量断言（WHY：钉住 CHART_W=598 / PAD_X=30，防未来改回 686 时测试仍绿）
    const ptStyle = (el: Element) => JSON.parse(el.getAttribute('data-style') ?? '{}')
    expect(ptStyle(document.querySelectorAll('.pt')[0]).left).toBe('30rpx')
    expect(ptStyle(document.querySelectorAll('.pt')[6]).left).toBe('568rpx')
  })
})
