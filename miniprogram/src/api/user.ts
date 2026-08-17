import { request } from './request'

/** 个人中心全量数据（GET /user/me，契约见方案设计文档-用户系统 5.2） */
export interface UserProfile {
  user: { id: number; nickname: string; avatar_text: string; coins: number }
  stats: { sessions: number; correct_rate: number; knowledge_points: number; total_correct: number }
  /** 近 7 天（含今日，今日在最后）每日答题数 */
  daily_answers: { date: string; count: number }[]
  /** 最近 10 个知识点，core=true 为核心标签（深色样式） */
  knowledge_tree: { name: string; core: boolean }[]
  /** 最近 5 条闯关 */
  recent_sessions: { id: number; topic: string; correct_rate: number; created_at: string }[]
}

/** 编辑资料（PUT /user/me）：仅昵称，头像自动取首字 */
export function updateMe(payload: { nickname: string }): Promise<UserProfile['user']> {
  return request({ url: '/user/me', method: 'PUT', data: payload })
}

/** 个人中心全量数据 */
export function getMe(): Promise<UserProfile> {
  return request({ url: '/user/me', method: 'GET' })
}
