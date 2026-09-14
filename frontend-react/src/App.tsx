import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import { GuestOnly, RequireAuth } from '@/components/common/RouteGuards'
import AppLayout from '@/components/layout/AppLayout'
import GeneratePage from '@/pages/Generate/GeneratePage'
import HomePage from '@/pages/Home/HomePage'
import LoginPage from '@/pages/Login/LoginPage'
import ProjectsPage from '@/pages/Projects/ProjectsPage'
import RegisterPage from '@/pages/Register/RegisterPage'

const router = createBrowserRouter([
  // 独立页面（无导航壳）：只允许未登录访问
  {
    path: '/login',
    element: (
      <GuestOnly>
        <LoginPage />
      </GuestOnly>
    ),
  },
  {
    path: '/register',
    element: (
      <GuestOnly>
        <RegisterPage />
      </GuestOnly>
    ),
  },
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      {
        path: 'generate',
        element: (
          <RequireAuth>
            <GeneratePage />
          </RequireAuth>
        ),
      },
      // 需要登录的页面：包一层 RequireAuth
      {
        path: 'projects',
        element: (
          <RequireAuth>
            <ProjectsPage />
          </RequireAuth>
        ),
      },
    ],
  },
])

/** 根组件：只负责路由装配 */
export default function App() {
  return <RouterProvider router={router} />
}
