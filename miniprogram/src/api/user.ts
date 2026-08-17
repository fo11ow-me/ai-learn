import { request } from './request'
import { AnswerRecord, Report } from '../types/report'
import { Quiz } from '../types/quiz'

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

/** 闯关结算请求（幂等键 + 用户输入原文 + 题库 + 作答；契约见方案设计文档-用户系统 5.2） */
export interface SessionSubmitPayload {
  session_key: string
  content: string
  quiz: Quiz
  answers: AnswerRecord[]
}

/** 闯关结算响应 */
export interface SessionSubmitResult {
  session_id: number
  /** 本关金币变动（防刷时为 0；可能为负） */
  coins_delta: number
  /** 本次是否计入金币 */
  coins_counted: boolean
  /** 最新余额 */
  coins_total: number
}

/** 闯关结算（POST /user/session，同步；未登录 401 时由 request 层重登重试） */
export function submitSession(payload: SessionSubmitPayload): Promise<SessionSubmitResult> {
  return request({ url: '/user/session', method: 'POST', data: payload })
}

/** 历史闯关详情（GET /user/session/{id}，契约见方案设计文档-用户系统 5.2） */
export interface SessionDetail {
  id: number
  topic: string
  content: string
  total_questions: number
  correct_count: number
  correct_rate: number
  coins_delta: number
  coins_counted: boolean
  quiz: Quiz
  answers: AnswerRecord[]
  /** 未生成（生成失败/未关联）时为 null */
  report: Report | null
  created_at: string
}

/** 历史闯关详情 */
export function getSession(id: number): Promise<SessionDetail> {
  return request({ url: `/user/session/${id}`, method: 'GET' })
}
