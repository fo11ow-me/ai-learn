import { Quiz } from '../types/quiz'
import { AnswerRecord, Report, TaskError, TaskStatus } from '../types/report'
import { request } from './request'

/** GET /report/{task_id} 响应（方案文档 4.1） */
export interface ReportTaskResponse {
  status: TaskStatus
  report?: Report
  error?: TaskError
}

/** POST /report 请求体：题库 + 作答记录 + 可选闯关记录 id（报告完成后回写） */
export interface ReportCreatePayload {
  quiz: Quiz
  answers: AnswerRecord[]
  /** 可选：闯关结算返回的 session_id（报告完成后回写报告到该记录） */
  session_id?: number
}

/** 创建报告任务（POST /report → 202 {task_id}） */
export function createReportTask(payload: ReportCreatePayload): Promise<{ task_id: string }> {
  return request({ url: '/report', method: 'POST', data: payload })
}

/** 查询报告任务状态 */
export function getReportTask(taskId: string): Promise<ReportTaskResponse> {
  return request({ url: `/report/${taskId}`, method: 'GET' })
}
