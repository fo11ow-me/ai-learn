export default defineAppConfig({
  pages: [
    'pages/index/index',
    'pages/profile/index',
    'pages/quiz/index',
    'pages/report/index',
    'pages/poster/index'
  ],
  window: {
    backgroundTextStyle: 'light',
    // 自定义导航（湖畔手账沉浸式风格，原型无系统导航栏），页面自处理顶部安全区
    navigationStyle: 'custom',
    navigationBarTitleText: 'AI 闯关学习',
    navigationBarTextStyle: 'black'
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
