/**
 * 项目工作区路由。
 *
 * 与制作台一致：服务端只出壳，内容组件关掉 SSR，因为它依赖登录态和浏览器端轮询。
 */

import dynamic from 'next/dynamic'

const ProjectWorkspace = dynamic(() => import('./ProjectWorkspace'), {
  ssr: false,
})

export default function ProjectWorkspacePage() {
  return <ProjectWorkspace />
}
