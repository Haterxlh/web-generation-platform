import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import AppLayout from '@/components/layout/AppLayout'
import GeneratePage from '@/pages/Generate/GeneratePage'
import HomePage from '@/pages/Home/HomePage'
import ProjectsPage from '@/pages/Projects/ProjectsPage'

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'generate', element: <GeneratePage /> },
      { path: 'projects', element: <ProjectsPage /> },
    ],
  },
])

/** 根组件：只负责路由装配 */
export default function App() {
  return <RouterProvider router={router} />
}
