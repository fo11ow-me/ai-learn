// 页面级恢复原生导航栏（WHY：全局 navigationStyle: custom 无返回键；子页面需原生返回键，tab 首页/我的保持沉浸式）
export default definePageConfig({
  navigationStyle: 'default',
  navigationBarTitleText: '分享海报'
})
