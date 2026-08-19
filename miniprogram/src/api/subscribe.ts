import { request } from './request'

/** 登记一条订阅推送配额（POST /user/subscribe；仅在 wx.requestSubscribeMessage 授权成功后调用） */
export function registerSubscribe(templateId: string): Promise<{ quota: number }> {
  return request({ url: '/user/subscribe', method: 'POST', data: { template_id: templateId } })
}
