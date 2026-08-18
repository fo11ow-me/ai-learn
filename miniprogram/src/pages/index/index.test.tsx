import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Taro from '@tarojs/taro'
import Index from './index'
import { createQuizTask } from '../../api/quiz'
import { getMe } from '../../api/user'
import type { UserProfile } from '../../api/user'

// vi.mock 工厂被提升，数据需经 vi.hoisted 提供
const { MOCK_QUIZ, EMPTY_PROFILE, NAV } = vi.hoisted(() => ({
  NAV: {
    // navigateTo 的 resolve 句柄（WHY：测试 2 模拟跳转动画中保持挂起，测试 3 模拟跳转完成后首页复位表单）
    resolve: null as (() => void) | null,
  },
  MOCK_QUIZ: {
    topic: '测试主题',
    source_summary: '摘要',
    questions: [
      { id: 1, type: 'single', question: 'q1', options: ['a', 'b', 'c', 'd'], answer: [0], explanation: 'e', knowledge_point: 'k' },
      { id: 2, type: 'single', question: 'q2', options: ['a', 'b', 'c', 'd'], answer: [1], explanation: 'e', knowledge_point: 'k' },
      { id: 3, type: 'multiple', question: 'q3', options: ['a', 'b', 'c', 'd'], answer: [0, 1], explanation: 'e', knowledge_point: 'k' },
      { id: 4, type: 'judge', question: 'q4', options: ['正确', '错误'], answer: [0], explanation: 'e', knowledge_point: 'k' },
      { id: 5, type: 'judge', question: 'q5', options: ['正确', '错误'], answer: [1], explanation: 'e', knowledge_point: 'k' },
    ],
  },
  EMPTY_PROFILE: {
    user: { id: 1, nickname: '测试', avatar_text: '测', coins: 0 },
    stats: { sessions: 0, correct_rate: 0, knowledge_points: 0, total_correct: 0 },
    daily_answers: [
      { date: '2026-08-12', count: 0 }, { date: '2026-08-13', count: 0 },
      { date: '2026-08-14', count: 0 }, { date: '2026-08-15', count: 0 },
      { date: '2026-08-16', count: 0 }, { date: '2026-08-17', count: 0 },
      { date: '2026-08-18', count: 0 },
    ],
    knowledge_tree: [],
    recent_sessions: [],
  } as UserProfile,
}))

vi.mock('@tarojs/taro', () => ({
  default: {
    navigateTo: vi.fn(() => new Promise((res) => (NAV.resolve = () => res({})))),
    setStorageSync: vi.fn(),
    removeStorageSync: vi.fn(),
    showToast: vi.fn(),
    getStorageSync: vi.fn(),
  },
  useDidShow: vi.fn(),
}))

vi.mock('@tarojs/components', () => ({
  View: (props: React.HTMLAttributes<HTMLDivElement>) => <div {...props} />,
  Text: (props: React.HTMLAttributes<HTMLSpanElement>) => <span {...props} />,
  // Taro 的 Textarea.onInput 事件参数是 { detail: { value } }，与原生 DOM input 事件不同，mock 在此适配（WHY：组件读 e.detail.value）
  Textarea: (props: React.HTMLAttributes<HTMLTextAreaElement>) => {
    const { onInput, ...rest } = props
    const taroOnInput = onInput as unknown as (e: { detail: { value: string } }) => void
    const { maxlength, ...restProps } = rest as React.HTMLAttributes<HTMLTextAreaElement> & { maxlength?: number }
    return (
      <textarea
        {...restProps}
        maxLength={maxlength}
        onInput={(e) => taroOnInput({ detail: { value: (e.target as HTMLTextAreaElement).value } })}
      />
    )
  },
  Button: (props: React.HTMLAttributes<HTMLButtonElement>) => <button {...props} />,
}))

vi.mock('../../api/quiz', () => ({
  createQuizTask: vi.fn(),
  getQuizTask: vi.fn(),
}))

vi.mock('../../api/user', () => ({
  getMe: vi.fn(),
  updateMe: vi.fn(),
}))

vi.mock('../../hooks/useStatusBarHeight', () => ({ useStatusBarHeight: () => 0 }))

// 轮询直接返回 completed（模拟出题完成，跳转依赖此状态）
vi.mock('../../hooks/usePollingTask', () => ({
  usePollingTask: () => ({ status: 'completed', data: MOCK_QUIZ, error: null }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  NAV.resolve = null
  vi.mocked(getMe).mockResolvedValue(EMPTY_PROFILE)
})

describe('首页出题完成跳转', () => {
  it('出题完成后跳转答题页并写入题库 Storage', () => {
    render(<Index />)
    expect(Taro.navigateTo).toHaveBeenCalledWith({ url: '/pages/quiz/index' })
    expect(Taro.setStorageSync).toHaveBeenCalledWith('quiz_data', MOCK_QUIZ)
  })

  it('出题完成瞬间不闪回首页（保持加载屏直到跳转）', async () => {
    vi.mocked(createQuizTask).mockResolvedValue({ task_id: 'task-1' })
    render(<Index />)
    fireEvent.input(screen.getByPlaceholderText('输入想学的知识，如：光的波粒二象性'), {
      target: { value: '测试内容' },
    })
    fireEvent.click(screen.getByText('开始漫步'))
    // 轮询完成（mock 直接 completed）：navigateTo 未完成（挂起）期间加载屏保持，首页表单不出现
    expect(await screen.findByText('正在铺就 5 道考题 · 约 15 秒')).not.toBeNull()
    expect(screen.queryByText('开始漫步')).toBeNull()
  })

  it('跳转答题页完成后首页复位为表单态（返回时不残留雾散加载屏）', async () => {
    vi.mocked(createQuizTask).mockResolvedValue({ task_id: 'task-1' })
    render(<Index />)
    fireEvent.input(screen.getByPlaceholderText('输入想学的知识，如：光的波粒二象性'), {
      target: { value: '测试内容' },
    })
    fireEvent.click(screen.getByText('开始漫步'))
    await screen.findByText('正在铺就 5 道考题 · 约 15 秒')
    // navigateTo 完成 → taskId 清空 → 首页恢复表单（WHY：从答题页返回时若 taskId 残留，generating 恒真卡在雾散屏）
    NAV.resolve!()
    await waitFor(() => expect(screen.queryByText('正在铺就 5 道考题 · 约 15 秒')).toBeNull())
    expect(screen.getByText('开始漫步')).not.toBeNull()
  })
})
