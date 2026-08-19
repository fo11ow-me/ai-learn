import { Question } from './quiz'

/** 错题重练契约类型（对齐后端 GET /user/review、POST /user/review/submit，见方案设计文档-用户系统 5.2） */

/** 一条待重温错题（question 为收录时的题目快照，含正确答案，服务端重练时按快照重判分） */
export interface ReviewItem {
  id: number
  question: Question
  question_type: Question['type']
  /** 知识点标签（收录自闯关结算） */
  knowledge_point: string
  /** 累计错过次数 */
  missed_count: number
  /** 当前连续答对次数（答对递增、答错归零） */
  correct_streak: number
  /** 下次复习时间（ISO；到期判定：next_review_at 当天 <= 今日） */
  next_review_at: string
  /** pending=待重温 / mastered=已掌握 */
  status: 'pending' | 'mastered'
}

/** 错题本页与「我的」页入口卡共用的全量数据 */
export interface ReviewBoard {
  summary: { due_count: number; mastered_count: number }
  /** 待重温列表（未到期的也会出现，是否可练由到期判定单独表达） */
  items: ReviewItem[]
  /** 明日起未来 7 天按日到期数 */
  schedule: { date: string; count: number }[]
}

/** 重练作答：item_id + 已选选项索引（与闯关 AnswerRecord 同心智；服务端按快照重判） */
export interface ReviewAttempt {
  item_id: number
  selected: number[]
}

/** 重练提交请求（POST /user/review/submit） */
export interface ReviewSubmitPayload {
  attempts: ReviewAttempt[]
}

/** 重练提交响应：逐条更新后的调度状态（不计金币、不调 AI） */
export interface ReviewSubmitResult {
  updated: {
    item_id: number
    status: 'pending' | 'mastered'
    correct_streak: number
    missed_count: number
    next_review_at: string
    mastered: boolean
    correct: boolean
  }[]
}
