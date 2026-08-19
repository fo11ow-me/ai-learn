export default defineAppConfig({
  pages: [
    'pages/index/index',
    'pages/profile/index',
    'pages/quiz/index',
    'pages/report/index',
    'pages/poster/index',
    'pages/knowledge-base/index',
    'pages/knowledge-base/detail',
    'pages/review/index'
  ],
  window: {
    backgroundTextStyle: 'light',
    // 自定义导航（湖畔手账沉浸式风格，原型无系统导航栏），页面自处理顶部安全区
    navigationStyle: 'custom',
    navigationBarTitleText: 'AI 闯关学习',
    navigationBarTextStyle: 'black',
    // 原生导航栏背景（WHY：默认 #000000 黑底，报告/海报页恢复 default 后黑底黑字会使返回键不可见；用 $paper 与页面背景融合）
    navigationBarBackgroundColor: '#F7F4ED'
  },
  tabBar: {
    color: '#6B7A72', // 晨雾灰（未选中）
    selectedColor: '#3A6B5C', // 湖水青碧（选中）
    backgroundColor: '#FFFDF8', // 纸页白
    borderStyle: 'white',
    list: [
      {
        pagePath: 'pages/index/index',
        text: '漫步',
        iconPath: 'assets/icons/tab-walk.png',
        selectedIconPath: 'assets/icons/tab-walk-active.png',
      },
      {
        pagePath: 'pages/profile/index',
        text: '我的',
        iconPath: 'assets/icons/tab-profile.png',
        selectedIconPath: 'assets/icons/tab-profile-active.png',
      },
    ],
  },
})
