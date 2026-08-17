import { Quiz } from '../types/quiz'
import { TaskError, TaskStatus } from '../types/report'
import { request } from './request'

/** GET /quiz/{task_id} 响应（方案文档 4.1） */
export interface QuizTaskResponse {
  status: TaskStatus
  quiz?: Quiz
  error?: TaskError
}

/** 创建出题任务（POST /quiz → 202 {task_id}） */
export function createQuizTask(content: string): Promise<{ task_id: string }> {
  return request({ url: '/quiz', method: 'POST', data: { content } })
}

/** 查询出题任务状态（前端 1.5s 轮询） */
export function getQuizTask(taskId: string): Promise<QuizTaskResponse> {
  return request({ url: `/quiz/${taskId}`, method: 'GET' })
}
