import { request } from './request'
import { ReviewBoard, ReviewSubmitPayload, ReviewSubmitResult } from '../types/review'

/** 错题本全量数据（GET /user/review，错题本页与「我的」页入口卡共用） */
export function getReview(): Promise<ReviewBoard> {
  return request({ url: '/user/review', method: 'GET' })
}

/** 重练提交（POST /user/review/submit，服务端按快照重判分 + 状态机更新，不计金币） */
export function submitReview(payload: ReviewSubmitPayload): Promise<ReviewSubmitResult> {
  return request({ url: '/user/review/submit', method: 'POST', data: payload })
}
