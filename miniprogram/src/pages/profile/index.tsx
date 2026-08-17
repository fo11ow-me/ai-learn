import { useCallback, useState } from 'react'
import { Button, Input, Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { getMe, updateMe, UserProfile } from '../../api/user'
import { useStatusBarHeight } from '../../hooks/useStatusBarHeight'
import { TaskError } from '../../types/report'
import './index.scss'

/** 星期缩写（原型图例：一 二 三 四 五 六 + 今） */
const WEEK_DAYS = ['日', '一', '二', '三', '四', '五', '六']

/** 相对时间文案（最近复盘列表：今天/昨天/N 天前） */
function relativeTime(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const diffDays = Math.floor((today.setHours(0, 0, 0, 0) - d.setHours(0, 0, 0, 0)) / 86400000)
  if (diffDays <= 0) return '今天'
  if (diffDays === 1) return '昨天'
  return `${diffDays} 天前`
}

export default function Profile() {
  const [data, setData] = useState<UserProfile | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [nickname, setNickname] = useState('')
  const [saving, setSaving] = useState(false)
  const statusBarHeight = useStatusBarHeight()
  const pageStyle = { paddingTop: `${statusBarHeight}px` }

  const load = useCallback(() => {
    setLoading(true)
    getMe()
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError((e as TaskError).message))
      .finally(() => setLoading(false))
  }, [])

  // 每次进入页面刷新（WHY：闯关后金币/记录可能刚变化，返回 tab 时数据需最新）
  useDidShow(() => {
    load()
  })

  const handleEdit = () => {
    setNickname(data?.user.nickname ?? '')
    setEditing(true)
  }

  const handleSave = async () => {
    const name = nickname.trim()
    if (!name) {
      Taro.showToast({ title: '昵称不能为空', icon: 'none' })
      return
    }
    setSaving(true)
    try {
      const user = await updateMe({ nickname: name })
      setData((prev) => (prev ? { ...prev, user } : prev))
      setEditing(false)
      Taro.showToast({ title: '已更新', icon: 'success' })
    } catch (e) {
      Taro.showToast({ title: (e as TaskError).message, icon: 'none' })
    } finally {
      setSaving(false)
    }
  }

  // 加载失败态（未登录/网络异常）：提示 + 重试
  if (error && !data) {
    return (
      <View className='page' style={pageStyle}>
        <View className='body'>
          <View className='chapter'>P1 · 我的林间日志</View>
          <View className='empty-card card'>
            <View className='empty-title'>加载失败</View>
            <View className='empty-sub'>{error}</View>
          </View>
          <Button className='btn-primary go-btn' onClick={load}>重新加载</Button>
        </View>
      </View>
    )
  }

  if (loading && !data) {
    return (
      <View className='page' style={pageStyle}>
        <View className='body'>
          <View className='chapter'>P1 · 我的林间日志</View>
        </View>
      </View>
    )
  }

  const profile = data as UserProfile
  const maxCount = Math.max(...profile.daily_answers.map((d) => d.count), 1)
  const empty = profile.stats.sessions === 0

  return (
    <View className='page' style={pageStyle}>
      <View className='body'>
        <View className='chapter'>P1 · 我的林间日志</View>

        {/* 用户信息区：文字头像 + 昵称 + 副标题 + 编辑入口（原型屏 1） */}
        <View className='profile'>
          <View className='avatar'>{profile.user.avatar_text}</View>
          <View className='info'>
            <View className='name-row'>
              <Text className='name'>{profile.user.nickname}</Text>
              <Text className='edit-btn' onClick={handleEdit}>✎ 编辑</Text>
            </View>
            <View className='sub'>
              已漫步 {profile.stats.sessions} 次 · 累计答对 {profile.stats.total_correct} 题 · 金币 {profile.user.coins}
            </View>
          </View>
        </View>

        {/* 统计卡 4 张（原型：闯关数/总正确率/掌握知识点/金币） */}
        <View className='stats-row'>
          <View className='stat card'><View className='v'>{profile.stats.sessions}</View><View className='l'>闯关数</View></View>
          <View className='stat card'><View className='v'>{profile.stats.correct_rate}%</View><View className='l'>总正确率</View></View>
          <View className='stat card'><View className='v'>{profile.stats.knowledge_points}</View><View className='l'>掌握知识点</View></View>
          <View className='stat card'><View className='v'>{profile.user.coins}</View><View className='l'>金币</View></View>
        </View>

        {empty ? (
          <>
            {/* 空态（需求文档 4.5：无记录时展示空态卡 + 去漫步引导） */}
            <View className='empty-card card'>
              <View className='empty-title'>暂无学习记录</View>
              <View className='empty-sub'>个人中心与学习分析已开放，先去湖畔完成第一次漫步吧</View>
            </View>
            <Button className='btn-primary go-btn' onClick={() => Taro.switchTab({ url: '/pages/index/index' })}>
              去漫步 <Text className='arrow'>→</Text>
            </Button>
          </>
        ) : (
          <>
            {/* 近七日答题柱状图（原型：今日晨光金高亮，其余湖水青碧渐变） */}
            <View className='sec-h'>近七日答题</View>
            <View className='chart card'>
              <View className='bars'>
                {profile.daily_answers.map((d, i) => {
                  const isToday = i === profile.daily_answers.length - 1
                  const height = Math.max((d.count / maxCount) * 100, d.count > 0 ? 6 : 2)
                  return (
                    <View key={d.date} className='bar-col'>
                      <View className={`bar ${isToday ? 'today' : ''}`} style={{ height: `${height}%` }} />
                      <View className='d'>{isToday ? '今' : WEEK_DAYS[new Date(d.date).getDay()]}</View>
                    </View>
                  )
                })}
              </View>
              <View className='legend'>
                <Text><View className='dot normal' />答题数</Text>
                <Text><View className='dot today-dot' />今日</Text>
              </View>
            </View>

            {/* 我的知识树（原型：树枝装饰 + 气泡标签，core 深色） */}
            <View className='sec-h'>我的知识树</View>
            <View className='tree card'>
              <View className='tree-branch'>
                <View className='twig twig-left' />
                <View className='twig twig-right' />
                <View className='node node-top' />
                <View className='node node-left' />
                <View className='node node-right' />
              </View>
              <View className='leaves'>
                {profile.knowledge_tree.map((kp) => (
                  <Text key={kp.name} className={`leaf ${kp.core ? 'core' : ''}`}>{kp.name}</Text>
                ))}
                {profile.knowledge_tree.length === 0 && <Text className='leaf'>等待第一片叶</Text>}
              </View>
            </View>

            {/* 最近复盘（原型：主题 + 相对时间 + 正确率，点击查看历史报告） */}
            <View className='sec-h'>最近复盘</View>
            <View className='card recent'>
              {profile.recent_sessions.map((s) => (
                <View
                  key={s.id}
                  className='row'
                  onClick={() => Taro.navigateTo({ url: `/pages/report/index?mode=history&id=${s.id}` })}
                >
                  <Text className='f'>{s.topic}</Text>
                  <View className='r'>
                    <Text className='s'>{relativeTime(s.created_at)} · {s.correct_rate}%</Text>
                    {s.correct_rate === 100 && <Text className='ok'>✓</Text>}
                  </View>
                </View>
              ))}
              {profile.recent_sessions.length === 0 && (
                <View className='row'><Text className='s'>还没有复盘记录</Text></View>
              )}
            </View>
          </>
        )}

        {/* 编辑昵称弹层（微信昵称填写能力 input type=nickname） */}
        {editing && (
          <View className='mask' onClick={() => setEditing(false)}>
            <View className='edit-card card' onClick={(e) => e.stopPropagation()}>
              <View className='edit-title'>编辑昵称</View>
              <Input
                className='edit-input'
                type='nickname'
                value={nickname}
                maxlength={16}
                placeholder='输入新昵称'
                onInput={(e) => setNickname(e.detail.value)}
              />
              <View className='edit-btns'>
                <Button className='btn-ghost' onClick={() => setEditing(false)}>取消</Button>
                <Button className='btn-primary' loading={saving} onClick={handleSave}>保存</Button>
              </View>
            </View>
          </View>
        )}
      </View>
    </View>
  )
}
