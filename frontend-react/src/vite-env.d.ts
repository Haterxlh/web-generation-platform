/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** FastAPI 后端基地址（留空时走 Vite 代理 /api） */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
