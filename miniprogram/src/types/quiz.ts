/**
 * 题库契约（方案文档 4.2，与后端 server/app/models/schemas.py 逐字段对齐——契约单一来源）
 */
export type QuestionType = 'single' | 'multiple' | 'judge'

export interface Question {
  /** 题号 1-5 递增 */
  id: number
  type: QuestionType
  /** 题干（judge 时为判断句） */
  question: string
  /** judge 固定为 ["正确","错误"]；single/multiple 各 4 项 */
  options: string[]
  /** 正确选项索引数组（judge 为 [0] 或 [1]；multiple 至少 2 个） */
  answer: number[]
  /** 深度讲解：为什么对、易错点在哪（答对答错都展示） */
  explanation: string
  /** 考察的知识点标签（≤10 字） */
  knowledge_point: string
}

export interface Quiz {
  /** 学习主题名（报告页标题） */
  topic: string
  /** AI 基于用户输入整理的知识摘要（供报告生成，不直接展示） */
  source_summary: string
  /** 5 题 = 2 单选 + 1 多选 + 2 判断 */
  questions: Question[]
}
