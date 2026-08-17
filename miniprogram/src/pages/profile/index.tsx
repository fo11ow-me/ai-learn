import { Button, Image, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useStatusBarHeight } from '../../hooks/useStatusBarHeight'
import leafIcon from '../../assets/icons/leaf.png'
import './index.scss'

/**
 * 「我的」页：P1 个人中心占位（MVP 无登录与持久化，展示空态）
 * 样式对齐扩展原型 02-extended-flow.html 屏 1「我的林间日志」，P1 接入真实数据
 */
export default function Profile() {
  const statusBarHeight = useStatusBarHeight()
  const pageStyle = { paddingTop: `${statusBarHeight}px` }

  return (
    <View className='page' style={pageStyle}>
      <View className='body'>
        <View className='chapter'>P1 · 我的林间日志</View>

        <View className='profile'>
          <View className='avatar'>林</View>
          <View>
            <View className='name'>林中客</View>
            <View className='sub'>已漫步 0 次 · 累计答对 0 题</View>
          </View>
        </View>

        <View className='stats-row'>
          <View className='stat card'>
            <View className='v'>0</View>
            <View className='l'>闯关数</View>
          </View>
          <View className='stat card'>
            <View className='v'>--</View>
            <View className='l'>总正确率</View>
          </View>
          <View className='stat card'>
            <View className='v'>0</View>
            <View className='l'>掌握知识点</View>
          </View>
        </View>

        <View className='empty-card card'>
          <Image className='empty-leaf' src={leafIcon} mode='aspectFit' />
          <View className='empty-title'>暂无学习记录</View>
          <View className='empty-sub'>个人中心与学习分析将在 P1 开放，先去湖畔完成第一次漫步吧</View>
        </View>

        <Button className='btn-primary go-btn' onClick={() => Taro.switchTab({ url: '/pages/index/index' })}>
          去漫步 <Text className='arrow'>→</Text>
        </Button>
      </View>
    </View>
  )
}
