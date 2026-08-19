import { useCallback, useEffect, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { getReview } from '../../api/review'
import { registerSubscribe } from '../../api/subscribe'
import { REVIEW_TMPL_ID } from '../../config'
import { useStatusBarHeight } from '../../hooks/useStatusBarHeight'
import { ReviewBoard, ReviewItem } from '../../types/review'
import { TaskError } from '../../types/report'
import './index.scss'

/** 题型中文标注（与答题页一致） */
const TYPE_LABEL: Record<string, string> = { single: '单选', multiple: '多选', judge: '判断' }

/** 到期判定（服务端契约：next_review_at 当天 <= 今日即到期，可进宝藏关卡） */
function isDue(item: ReviewItem): boolean {
  const d = new Date(item.next_review_at)
  const today = new Date()
  d.setHours(0, 0, 0, 0)
  today.setHours(0, 0, 0, 0)
  return d.getTime() <= today.getTime()
}

/** 下次复习相对文案（原型「明日/后天/3 天后」；跨月显示 M/D） */
function nextLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  d.setHours(0, 0, 0, 0)
  today.setHours(0, 0, 0, 0)
  const diff = Math.round((d.getTime() - today.getTime()) / 86400000)
  if (diff <= 0) return '今日'
  if (diff === 1) return '明日'
  if (diff === 2) return '后天'
  if (diff < 30) return `${diff} 天后`
  return `${d.getMonth() + 1}/${d.getDate()}`
}

/** 复习安排日期文案（明日起未来 7 天，跨周带星期） */
function dayLabel(dateStr: string): string {
  const d = new Date(`${dateStr}T00:00:00`)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  d.setHours(0, 0, 0, 0)
  const diff = Math.round((d.getTime() - today.getTime()) / 86400000)
  if (diff === 1) return '明日'
  if (diff === 2) return '后天'
  return `${d.getMonth() + 1}/${d.getDate()}（周${'日一二三四五六'[d.getDay()]}）`
}

/** 自绘导航条（与知识库页同构：返回键 + 标题） */
function NavBar() {
  return (
    <View className='nav-bar'>
      <View className='nav-back' onClick={() => Taro.navigateBack({ fail: () => Taro.switchTab({ url: '/pages/index/index' }) })}>
        <View className='nav-back-icon' />
      </View>
      <Text className='nav-title'>旧识重温</Text>
      <View className='nav-side' />
    </View>
  )
}

export default function ReviewPage() {
  const [board, setBoard] = useState<ReviewBoard | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const statusBarHeight = useStatusBarHeight()
  const pageStyle = { paddingTop: `${statusBarHeight}px` }

  const load = useCallback(() => {
    setLoading(true)
    getReview()
      .then((d) => { setBoard(d); setError('') })
      .catch((e) => setError((e as TaskError).message))
      .finally(() => setLoading(false))
  }, [])

  // 首次挂载必加载（WHY：H5 刷新时 onShow 早于挂载，useDidShow 会丢失）
  useEffect(load, [load])
  // 从宝藏关卡返回时刷新（重练后调度已更新）
  useDidShow(load)

  /** 订阅复习提醒（仅微信环境；wx.requestSubscribeMessage 授权成功才登记配额，拒绝静默） */
  const handleSubscribe = async () => {
    try {
      // entityIds 仅支付宝端使用（Taro 4 的 AtLeastOne 类型要求两字段，微信端忽略）
      const res = await Taro.requestSubscribeMessage({ tmplIds: [REVIEW_TMPL_ID], entityIds: [] })
      if (res[REVIEW_TMPL_ID] === 'accept') {
        const { quota } = await registerSubscribe(REVIEW_TMPL_ID)
        Taro.showToast({ title: `订阅成功 · 剩余 ${quota} 次提醒`, icon: 'none' })
      }
      // 'reject'/'ban'：用户拒绝或永久拒绝，静默不打扰
    } catch {
      // 取消授权/开发者工具不支持：静默
    }
  }

  if (error && !board) {
    return (
      <View className='page' style={pageStyle}>
        <NavBar />
        <View className='body'>
          <View className='chapter'>P1 · 旧识重温</View>
          <View className='empty-card card'>
            <View className='empty-title'>加载失败</View>
            <View className='empty-sub'>{error}</View>
          </View>
          <Button className='btn-primary go-btn' onClick={load}>重新加载</Button>
        </View>
      </View>
    )
  }

  if (loading && !board) {
    return (
      <View className='page' style={pageStyle}>
        <NavBar />
        <View className='body'>
          <View className='chapter'>P1 · 旧识重温</View>
          <View className='empty'>加载中…</View>
        </View>
      </View>
    )
  }

  const { summary, items, schedule } = board as ReviewBoard
  const dueToday = items.filter((i) => isDue(i)).length

  return (
    <View className='page' style={pageStyle}>
      <NavBar />
      <View className='body'>
        <View className='chapter'>P1 · 旧识重温</View>
        <Text className='page-title'>错题本</Text>
        <Text className='page-sub'>错过的不必重来，但值得再见一次</Text>

        {/* 艾宾浩斯提醒 banner（原型 review-banner；去重温在宝藏关卡接通后跳转） */}
        <View className='review-banner'>
          <View className='bell'>⏳</View>
          <View className='banner-text'>
            <View className='t'>
              {summary.due_count > 0 ? `${summary.due_count} 道错题到了重温之日` : '今日无待重温错题'}
            </View>
            <View className='s'>今天复习，记忆保留率可提升 62%</View>
          </View>
          <Text className='go'>{dueToday > 0 ? '去重温 〉' : ''}</Text>
        </View>

        {/* 待重温错题列表（全部 pending 条目；未到期条目标注下次复习时间） */}
        <View className='sec-h'>待重温 · {summary.due_count} 道</View>
        {items.map((item) => (
          <View key={item.id} className='card mistake-card'>
            <View className='q'>{item.question.question}（{TYPE_LABEL[item.question_type]}）</View>
            <View className='meta'>
              <Text className='kp'>{item.knowledge_point || '未标注知识点'}</Text>
              <Text className='when'>第 {item.missed_count} 次错过 · 下次：{nextLabel(item.next_review_at)}</Text>
            </View>
          </View>
        ))}
        {items.length === 0 && (
          <View className='empty-card card'>
            <View className='empty-title'>暂无错题</View>
            <View className='empty-sub'>继续保持，去闯关赚取更多金币吧</View>
            <Button className='btn-primary' onClick={() => Taro.switchTab({ url: '/pages/index/index' })}>
              去闯关 <Text className='arrow'>→</Text>
            </Button>
          </View>
        )}

        {/* 未来 7 天复习安排（按日分组；当天有到期错题则列出知识点） */}
        <View className='sec-h'>复习安排</View>
        <View className='card recent'>
          {schedule.map((day) => {
            const dayItems = items.filter((i) => i.next_review_at.slice(0, 10) === day.date)
            return (
              <View key={day.date} className='row'>
                <Text className='f'>📅 {dayLabel(day.date)} · {day.count} 道</Text>
                <Text className='s'>{dayItems.map((i) => i.knowledge_point || '错题').join(' · ')}</Text>
              </View>
            )
          })}
          {schedule.every((d) => d.count === 0) && (
            <View className='row'><Text className='s'>未来 7 天没有安排，去闯关创造一次相遇吧</Text></View>
          )}
        </View>

        {/* 订阅复习提醒（仅微信环境且已配置模板 ID；拒绝授权静默） */}
        {Taro.getEnv() !== 'WEB' && REVIEW_TMPL_ID && (
          <Button className='btn-ghost subscribe-btn' onClick={handleSubscribe}>
            🔔 订阅复习提醒
          </Button>
        )}

        {/* 开启宝藏关卡（到期 0 时禁用；任务 10.1 接通跳转） */}
        <Button
          className='btn-primary'
          disabled={dueToday === 0}
          onClick={() => {
            if (dueToday === 0) {
              Taro.showToast({ title: '今日无待重温错题', icon: 'none' })
            }
          }}
        >
          开启“宝藏关卡” <Text className='arrow'>→</Text>
        </Button>
      </View>
    </View>
  )
}
