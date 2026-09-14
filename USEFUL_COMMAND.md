---
name: useful-commands
description: 项目常用命令, 比如前端和后端的启动命令
metadata:
  version: "1.0"
---

### 1. 前端

1.1 前端启动命令

```shell
cd ./frontend-react
npm run dev
```

> Local: http://localhost:5173/

### 2. 后端

2.1 后端启动命令

```shell
cd ./backend-uv-fastapi
uv run fastapi dev
```

> Server started at http://127.0.0.1:8000
> Documentation at http://127.0.0.1:8000/docs

2.2 后端测试命令

```shell
uv run pytest -q
```

> -q 含义：quiet，静默模式，不输出测试详情
