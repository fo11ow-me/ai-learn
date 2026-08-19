import { useCallback, useEffect, useState } from 'react'
import { Button, Input, Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  KnowledgeBase,
  listKnowledgeBases,
  updateKnowledgeBase,
} from '../../api/knowledgeBase'
import { useStatusBarHeight } from '../../hooks/useStatusBarHeight'
import { TaskError } from '../../types/report'
import './index.scss'

/** 自绘导航条（与答题页同构：返回键 + 标题） */
function NavBar() {
  return (
    <View className='nav-bar'>
      <View className='nav-back' onClick={() => Taro.navigateBack({ fail: () => Taro.switchTab({ url: '/pages/index/index' }) })}>
        <View className='nav-back-icon' />
      </View>
      <Text className='nav-title'>知识库</Text>
      <View className='nav-side' />
    </View>
  )
}

export default function KnowledgeBaseList() {
  const [items, setItems] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(true)
  const [modal, setModal] = useState<{ editing: KnowledgeBase | null } | null>(null)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const statusBarHeight = useStatusBarHeight()
  const pageStyle = { paddingTop: `${statusBarHeight}px` }

  const load = useCallback(() => {
    setLoading(true)
    listKnowledgeBases()
      .then((d) => setItems(d.items))
      .catch((e) => Taro.showToast({ title: (e as TaskError).message, icon: 'none' }))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load]) // 首次挂载必加载（WHY：H5 刷新时 onShow 早于挂载，useDidShow 会丢失）
  useDidShow(load) // 从详情页返回时刷新（上传进度/删除后数据最新）

  /** 打开新建/重命名弹层 */
  const openModal = (editing: KnowledgeBase | null) => {
    setDraft(editing?.name ?? '')
    setModal({ editing })
  }

  /** 提交新建/重命名 */
  const handleSave = async () => {
    const name = draft.trim()
    if (!name) {
      Taro.showToast({ title: '请输入知识库名称', icon: 'none' })
      return
    }
    setSaving(true)
    try {
      if (modal?.editing) {
        await updateKnowledgeBase(modal.editing.id, { name })
      } else {
        await createKnowledgeBase(name)
      }
      setModal(null)
      load()
    } catch (e) {
      Taro.showToast({ title: (e as TaskError).message, icon: 'none' })
    } finally {
      setSaving(false)
    }
  }

  /** 删除知识库（级联删文档，需二次确认） */
  const handleDelete = (kb: KnowledgeBase) => {
    Taro.showModal({
      title: '删除知识库',
      content: `「${kb.name}」及其全部文档将被删除，此操作不可恢复`,
      confirmColor: '#B3594C',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await deleteKnowledgeBase(kb.id)
          load()
        } catch (e) {
          Taro.showToast({ title: (e as TaskError).message, icon: 'none' })
        }
      },
    })
  }

  return (
    <View className='page' style={pageStyle}>
      <NavBar />
      <View className='body'>
        <View className='chapter'>CHAP. 04 · 藏书阁</View>
        <Text className='page-title'>你的私有知识库</Text>
        <Text className='page-sub'>上传文档，AI 出题优先基于你的资料</Text>

        {loading && <View className='empty'>加载中…</View>}
        {!loading && items.length === 0 && (
          <View className='empty'>还没有知识库，点击下方按钮创建第一个</View>
        )}

        {items.map((kb) => (
          <View
            key={kb.id}
            className='kb-card'
            onClick={() => Taro.navigateTo({ url: `/pages/knowledge-base/detail?id=${kb.id}&name=${encodeURIComponent(kb.name)}` })}
          >
            <View className='kb-info'>
              <Text className='kb-name'>{kb.name}</Text>
              {kb.description && <Text className='kb-desc'>{kb.description}</Text>}
              <Text className='kb-meta'>
                {kb.ready_count} 份可出题 · 共 {kb.doc_count} 份文档
              </Text>
            </View>
            <View className='kb-actions'>
              <Text className='kb-edit' onClick={(e) => { e.stopPropagation(); openModal(kb) }}>重命名</Text>
              <Text className='kb-delete' onClick={(e) => { e.stopPropagation(); handleDelete(kb) }}>删除</Text>
            </View>
            <View className='kb-arrow'>›</View>
          </View>
        ))}

        <Button className='btn-primary add-btn' onClick={() => openModal(null)}>
          ＋ 新建知识库
        </Button>
      </View>

      {/* 新建/重命名弹层 */}
      {modal && (
        <View className='mask' onClick={() => setModal(null)}>
          <View className='modal' onClick={(e) => e.stopPropagation()}>
            <Text className='modal-title'>{modal.editing ? '重命名知识库' : '新建知识库'}</Text>
            <Input
              className='modal-input'
              placeholder='输入名称（≤32 字）'
              maxlength={32}
              value={draft}
              focus
              onInput={(e) => setDraft(e.detail.value)}
            />
            <View className='modal-actions'>
              <Button className='btn-ghost' onClick={() => setModal(null)}>取消</Button>
              <Button className='btn-primary' loading={saving} disabled={saving} onClick={handleSave}>
                保存
              </Button>
            </View>
          </View>
        </View>
      )}
    </View>
  )
}
