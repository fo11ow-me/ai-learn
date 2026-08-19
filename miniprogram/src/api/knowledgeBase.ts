import Taro from '@tarojs/taro'
import { BASE_URL } from '../config'
import { TaskError, TaskStatus } from '../types/report'
import { getToken } from '../utils/auth'
import { request } from './request'

/** 知识库（GET /knowledge-base 列表项；doc_count 全部文档数，ready_count 可出题文档数） */
export interface KnowledgeBase {
  id: number
  name: string
  description: string
  doc_count: number
  ready_count: number
  created_at: string
}

/** 知识库文档（uploading=解析中 / ready=可用 / failed=失败可重试） */
export interface KnowledgeDocument {
  id: number
  filename: string
  file_type: string
  status: 'uploading' | 'ready' | 'failed'
  chunk_count: number
  created_at: string
}

/** 上传/重建任务轮询响应（复用 TaskStore，与 quiz/report 同一 202+轮询心智模型） */
export interface KbTaskResponse {
  status: TaskStatus
  document_id?: number
  chunk_count?: number
  error?: TaskError
}

/** 知识库列表 */
export function listKnowledgeBases(): Promise<{ items: KnowledgeBase[] }> {
  return request({ url: '/knowledge-base', method: 'GET' })
}

/** 创建知识库（同用户重名 → 409 NAME_EXISTS） */
export function createKnowledgeBase(name: string, description = ''): Promise<KnowledgeBase> {
  return request({ url: '/knowledge-base', method: 'POST', data: { name, description } })
}

/** 重命名/改描述 */
export function updateKnowledgeBase(
  id: number,
  patch: { name?: string; description?: string },
): Promise<KnowledgeBase> {
  return request({ url: `/knowledge-base/${id}`, method: 'PATCH', data: patch })
}

/** 删除知识库（级联删文档与向量索引） */
export function deleteKnowledgeBase(id: number): Promise<void> {
  return request({ url: `/knowledge-base/${id}`, method: 'DELETE' })
}

/** 文档列表 */
export function listDocuments(kbId: number): Promise<{ items: KnowledgeDocument[] }> {
  return request({ url: `/knowledge-base/${kbId}/document`, method: 'GET' })
}

/**
 * 上传文档（multipart；后端 202 → 轮询 KbTaskResponse 感知解析进度）。
 * WHY 不走 request()：Taro 文件上传必须用 uploadFile，此处自行注入 token 并做同一错误解析
 */
export function uploadDocument(kbId: number, filePath: string): Promise<{ task_id: string; document_id: number }> {
  return new Promise((resolve, reject) => {
    Taro.uploadFile({
      url: `${BASE_URL}/knowledge-base/${kbId}/document`,
      filePath,
      name: 'file',
      header: { Authorization: `Bearer ${getToken()}` },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(JSON.parse(res.data) as { task_id: string; document_id: number })
          return
        }
        const detail = (JSON.parse(res.data || '{}') as { detail?: { code?: string; message?: string } }).detail
        reject({
          code: detail?.code || 'HTTP_ERROR',
          message: detail?.message || `上传失败（${res.statusCode}）`,
        } as TaskError)
      },
      fail: () => reject({ code: 'NETWORK_ERROR', message: '网络连接失败，请检查网络' } as TaskError),
    })
  })
}

/** 删除文档 */
export function deleteDocument(docId: number): Promise<void> {
  return request({ url: `/knowledge-base/document/${docId}`, method: 'DELETE' })
}

/** 重建索引（Chroma 损坏/丢失时按 MySQL 全文重建；202 → 轮询） */
export function reindexDocument(docId: number): Promise<{ task_id: string }> {
  return request({ url: `/knowledge-base/document/${docId}/reindex`, method: 'POST' })
}

/** 上传/重建任务状态轮询 */
export function getKbTask(taskId: string): Promise<KbTaskResponse> {
  return request({ url: `/knowledge-base/task/${taskId}`, method: 'GET' })
}
