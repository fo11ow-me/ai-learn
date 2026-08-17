/**
 * 报告与任务状态契约（方案文档 4.1/4.3，与后端 schemas.py 对齐——契约单一来源）
 */

/** 作答记录：题目 id + 已选选项索引数组 */
export interface AnswerRecord {
  question_id: number
  selected: number[]
}

export interface MasteryItem {
  knowledge_point: string
  /** 掌握度 0-100 */
  level: number
  /** 针对该用户的点评 */
  comment: string
}

export interface Report {
  /** 正确率 0-100 */
  correct_rate: number
  correct_count: number
  total_questions: number
  /** 知识总结 200~300 字 */
  summary: string
  /** 逐知识点掌握度 */
  mastery: MasteryItem[]
  /** 2~3 条可执行学习建议 */
  suggestions: string[]
  /** 学习金句 ≤30 字（海报用） */
  quote: string
}

/** 任务状态枚举（方案文档 4.1） */
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed'

/** 任务失败信息：错误码 + 用户可读文案 */
export interface TaskError {
  /** LLM_TIMEOUT / LLM_PARSE_FAILED / LLM_UNAVAILABLE / TASK_TIMEOUT / SENSITIVE_CONTENT */
  code: string
  message: string
}
