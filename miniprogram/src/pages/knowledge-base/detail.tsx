import { useCallback, useEffect, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import {
  deleteDocument,
  getKbTask,
  KnowledgeDocument,
  listDocuments,
  reindexDocument,
  uploadDocument,
} from '../../api/knowledgeBase'
import { useStatusBarHeight } from '../../hooks/useStatusBarHeight'
import { TaskError } from '../../types/report'
import './detail.scss'

/** 状态徽标（解析中/可用/失败）文案与样式 */
const STATUS_LABEL: Record<KnowledgeDocument['status'], string> = {
  uploading: '解析中…',
  ready: '可用',
  failed: '解析失败',
}

/** 相对时间（上传列表展示：今天/昨天/N 天前） */
function relativeTime(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const diffDays = Math.floor((today.setHours(0, 0, 0, 0) - d.setHours(0, 0, 0, 0)) / 86400000)
  if (diffDays <= 0) return '今天'
  if (diffDays === 1) return '昨天'
  return `${diffDays} 天前`
}

export default function KnowledgeBaseDetail() {
  const router = Taro.useRouter()
  const kbId = Number(router.params.id)
  const kbName = decodeURIComponent(router.params.name ?? '知识库')
  const [docs, setDocs] = useState<KnowledgeDocument[]>([])
  const [loading, setLoading] = useState(true)
  /** 是否有文档解析中（WHY：解析中禁止重复上传——同名覆盖会打断既有任务，任务 5.4） */
  const [uploading, setUploading] = useState(false)
  const statusBarHeight = useStatusBarHeight()
  const pageStyle = { paddingTop: `${statusBarHeight}px` }

  const load = useCallback(() => {
    setLoading(true)
    listDocuments(kbId)
      .then((d) => setDocs(d.items))
      .catch((e) => Taro.showToast({ title: (e as TaskError).message, icon: 'none' }))
      .finally(() => setLoading(false))
  }, [kbId])

  useEffect(load, [load])

  /** 选择并上传文档：chooseMessageFile → 202 → 轮询任务 → 刷新列表 */
  const handleUpload = async () => {
    if (uploading) return
    try {
      const res = await Taro.chooseMessageFile({
        count: 1,
        type: 'file',
        extension: ['pdf', 'docx', 'md', 'txt'],
      })
      const file = res.tempFiles[0]
      if (!file) return
      setUploading(true)
      const { task_id: taskId } = await uploadDocument(kbId, file.path)
      await pollTask(taskId)
    } catch (e) {
      // 用户取消选择（errMsg 含 cancel）不提示；其余错误提示
      const err = e as TaskError & { errMsg?: string }
      if (!err?.errMsg?.includes('cancel')) {
        Taro.showToast({ title: err?.message || '上传失败', icon: 'none' })
      }
    } finally {
      setUploading(false)
      load()
    }
  }

  /** 轮询上传任务直到 completed/failed（1.5s 间隔，超时 60s） */
  const pollTask = async (taskId: string) => {
    for (let i = 0; i < 40; i++) {
      const resp = await getKbTask(taskId)
      if (resp.status === 'completed') {
        Taro.showToast({ title: '上传完成，可出题', icon: 'success' })
        return
      }
      if (resp.status === 'failed') {
        Taro.showToast({ title: resp.error?.message || '解析失败，请重新上传', icon: 'none' })
        return
      }
      await new Promise((r) => setTimeout(r, 1500))
    }
    Taro.showToast({ title: '解析超时，请稍后刷新查看', icon: 'none' })
  }

  /** 失败文档重新上传（同名覆盖：旧索引替换为本次内容；WHY 不传 doc：新文件路径由 chooseMessageFile 决定） */
  const handleReupload = async () => {
    try {
      const res = await Taro.chooseMessageFile({
        count: 1,
        type: 'file',
        extension: ['pdf', 'docx', 'md', 'txt'],
      })
      const file = res.tempFiles[0]
      if (!file) return
      setUploading(true)
      const { task_id: taskId } = await uploadDocument(kbId, file.path)
      await pollTask(taskId)
    } catch (e) {
      const err = e as TaskError & { errMsg?: string }
      if (!err?.errMsg?.includes('cancel')) {
        Taro.showToast({ title: err?.message || '重新上传失败', icon: 'none' })
      }
    } finally {
      setUploading(false)
      load()
    }
  }

  /** 删除文档（先删索引后删记录，后端保证无幽灵向量） */
  const handleDelete = (doc: KnowledgeDocument) => {
    Taro.showModal({
      title: '删除文档',
      content: `「${doc.filename}」将被删除`,
      confirmColor: '#B3594C',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await deleteDocument(doc.id)
          load()
        } catch (e) {
          Taro.showToast({ title: (e as TaskError).message, icon: 'none' })
        }
      },
    })
  }

  /** 重建索引（向量索引丢失/损坏时按全文重建） */
  const handleReindex = async (doc: KnowledgeDocument) => {
    try {
      const { task_id: taskId } = await reindexDocument(doc.id)
      setUploading(true)
      await pollTask(taskId)
    } catch (e) {
      Taro.showToast({ title: (e as TaskError).message, icon: 'none' })
    } finally {
      setUploading(false)
      load()
    }
  }

  return (
    <View className='page' style={pageStyle}>
      <View className='nav-bar'>
        <View className='nav-back' onClick={() => Taro.navigateBack({ fail: () => Taro.reLaunch({ url: '/pages/index/index' }) })}>
          <View className='nav-back-icon' />
        </View>
        <Text className='nav-title'>{kbName}</Text>
        <View className='nav-side' />
      </View>

      <View className='body'>
        <Text className='page-sub'>上传 PDF / Word / Markdown / txt 文档，AI 出题将优先基于这些资料</Text>

        <Button
          className='btn-primary upload-btn'
          onClick={handleUpload}
          loading={uploading}
          disabled={uploading}
        >
          {uploading ? '解析中，请稍候…' : '＋ 上传文档'}
        </Button>

        {loading && <View className='empty'>加载中…</View>}
        {!loading && docs.length === 0 && (
          <View className='empty'>还没有文档，上传第一份资料开始构建知识库</View>
        )}

        {docs.map((doc) => (
          <View key={doc.id} className='doc-card'>
            <View className='doc-main'>
              <Text className='doc-name'>{doc.filename}</Text>
              <View className='doc-meta'>
                <Text className={`doc-status ${doc.status}`}>{STATUS_LABEL[doc.status]}</Text>
                <Text className='doc-time'>{relativeTime(doc.created_at)}</Text>
                {doc.status === 'ready' && <Text className='doc-chunks'>{doc.chunk_count} 段</Text>}
              </View>
              {doc.status === 'failed' && (
                <Text className='doc-fail-hint'>解析失败（可能是扫描版/无文本层），请重新上传可复制文本的文档</Text>
              )}
            </View>
            <View className='doc-actions'>
              {doc.status === 'failed' && (
                <Text className='doc-action' onClick={handleReupload}>重新上传</Text>
              )}
              {doc.status === 'ready' && (
                <Text className='doc-action' onClick={() => handleReindex(doc)}>重建索引</Text>
              )}
              <Text className='doc-action danger' onClick={() => handleDelete(doc)}>删除</Text>
            </View>
          </View>
        ))}
      </View>
    </View>
  )
}
