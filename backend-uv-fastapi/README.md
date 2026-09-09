# backend-uv-fastapi

Web 生成平台的后端服务，使用 **FastAPI** 实现接口，依赖由 **uv** 管理。

## 项目结构

```
backend-uv-fastapi/
├── app/                      # 主应用目录（Python 包）
│   ├── main.py               # FastAPI 应用入口（app = FastAPI()）
│   ├── api/                  # API 路由层（表现层）：HTTP 路由与请求/响应处理
│   ├── core/                 # 核心配置与工具：应用配置、数据库会话、依赖注入
│   ├── models/               # 数据模型层：Pydantic 模型与 ORM 实体
│   ├── repositories/         # 数据访问层：封装数据库 CRUD 操作
│   ├── services/             # 业务逻辑层：核心业务规则，组合 repositories
│   └── utils/                # 通用工具函数
├── tests/                    # 测试代码目录（pytest）
├── pyproject.toml            # 项目配置（依赖、入口、pytest 配置）
├── uv.lock                   # 依赖锁文件（勿手动编辑）
├── .python-version           # Python 版本（3.12）
└── README.md
```

## 分层调用约定

请求按如下方向流转，**禁止跨层调用**（如路由直接写 SQL）：

```
api (路由) → services (业务逻辑) → repositories (数据访问) → 数据库
                        ↑
                    models (数据模型)
```

| 层 | 职责 | 说明 |
| --- | --- | --- |
| `api/` | 表现层 | 定义路由，参数校验与结果组装，业务下沉到 services |
| `core/` | 核心层 | 应用配置、数据库会话等跨层基础组件 |
| `models/` | 模型层 | 接口出入参（Pydantic）与持久化实体（ORM） |
| `repositories/` | 数据访问层 | 每个实体一个 Repository，封装 CRUD |
| `services/` | 业务逻辑层 | 核心业务用例，被路由层调用 |
| `utils/` | 工具层 | 与业务无关的通用函数 |

## 环境要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)（包与虚拟环境管理）
- Python 虚拟环境（.venv）
- Python 解释器（.venv/Scripts/python）

## 快速启动

```bash
# 1. 安装依赖（自动创建 .venv，含 dev 测试依赖）
uv sync

# 2. 启动开发服务器（自动重载）
uv run fastapi dev
```

- 默认地址：<http://127.0.0.1:8000>
- 接口文档（Swagger UI）：<http://127.0.0.1:8000/docs>

> 入口由 `pyproject.toml` 的 `[tool.fastapi] entrypoint = "app.main:app"` 指定。

## 运行测试

```bash
uv run pytest
```

## 代码规范

- 命名：模块/变量/函数 `snake_case`，类（含 Pydantic 模型）`PascalCase`，常量 `UPPER_SNAKE_CASE`
- 路由遵循 RESTful 风格，请求/响应使用 Pydantic `BaseModel` 校验，字段提供中文描述
- 接入数据库时使用 ORM 或参数化语句，禁止拼接 SQL 字符串
- 敏感信息（数据库密码、API Key 等）通过环境变量注入，严禁硬编码
