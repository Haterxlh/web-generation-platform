# 项目进度 —— backend-uv-fastapi

> 本文件用于跨会话同步开发进度。每次总结进度时按此格式更新。
> 最近更新时间：2026-09-10

## 1. 模块进度

### 模块：基础设施（MySQL 连接与会话）
- **状态**：已完成
- **功能范围**：建立 FastAPI 与本地 MySQL 的连接、会话管理与建表能力
- **已交付内容**：
  - 核心文件：
    - `app/core/mysql_config.py`（读取 `.env` 的 MySQL 配置并拼连接串）
    - `app/core/mysql_db.py`（engine / sessionmaker / `MysqlBase` 基类 / `get_mysql_db()` 依赖）
    - `app/utils/create_all_table.py`（开发期建表脚本，需先 import 各模型）
  - `.env`：`MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB_NAME`（已被 .gitignore 忽略）
- **关键决策**：
  - 连接串走环境变量，敏感信息不进代码/git
  - `extra="ignore"` 允许一份 `.env` 被多个 Settings 类共享
  - `Base` 命名为 `MysqlBase`（用户自定义）
- **验证情况**：已实测连接本机 MySQL 成功（`SELECT 1` 通过）；`wgp_db` 库连通
- **待办与遗留**：无

### 模块：用户模块（注册 / 登录 / 当前用户）
- **状态**：已完成
- **功能范围**：用户注册、登录签发 JWT、携带 token 获取当前用户
- **已交付内容**：
  - 接口：
    - `POST /api/user/register` —— 注册（账号查重，409）
    - `POST /api/user/login` —— 登录（返回 JWT + 用户信息）
    - `GET /api/user/current` —— 获取当前登录用户（Bearer token 校验）
  - 核心文件：
    - `app/models/user.py`（User ORM 实体，11 列，映射 `user` 表）
    - `app/schemas/user_schemas.py`（RegisterRequest / LoginRequest / UserResponse / LoginResponse，响应白名单不含密码）
    - `app/utils/security.py`（bcrypt 哈希、JWT 签发/解析）
    - `app/core/jwt_config.py`（JWT_SECRET / HS256 / 过期时间）
    - `app/utils/parse_token.py`（`get_current_user` 登录校验依赖）
    - `app/repositories/user_repository.py`（create / get_by_user_account / get_by_id）
    - `app/services/user_service.py`（register / login 业务规则）
    - `app/api/user.py`（用户路由）
    - `app/main.py`（挂载用户路由，替换原脚手架示例）
    - `tests/test_smoke.py`、`tests/test_user_service.py`（初步测试文件）
- **关键决策**：
  - 密码使用 bcrypt 加盐哈希存储，绝不存明文
  - 认证采用 JWT 无状态方案（pyjwt），密钥走 `.env`
  - 建表采用 `Base.metadata.create_all()`，**模型为唯一真源**；尚未引入 Alembic
  - 逻辑删除：所有查询过滤 `is_delete == 0`
  - ORM 列名显式映射驼峰数据库列（`mapped_column("userAccount", ...)`）
  - docstring 统一 Google 风格（用户要求）
  - 文件名用户自定义：`user_schemas.py`、`parse_token.py`（未严格按 README 默认命名）
- **验证情况**：端到端 5 项验证全部通过（注册 200 无密码字段 / 重复注册 409 / 登录 200 拿 token / 带 token 访问 current 200 / 库中密码为 `$2b$12$...` 哈希）
- **待办与遗留**：
  - `tests/` 尚不完整：`test_smoke.py` 断言根路由返回 `{"Hello": "World"}`，但 `main.py` 已删除该示例路由，**该用例预计失败**；`test_user_service.py` 无断言。需补全测试并跑 `uv run pytest`
  - 前端对接未开始（注册/登录页面、token 存储、请求拦截器）
  - 模型字段后续变更时需引入 Alembic 迁移（`create_all` 不能改已存在的表）
  - `get_current_user` 目前位于 `app/utils/parse_token.py`，按分层约定属接口层依赖，后续可评估是否迁回 `api/deps.py`

## 2. 项目级约定（跨模块通用）
- 后端分层调用方向：`api → services → repositories → 数据库`，禁止跨层调用
- 依赖管理：uv（依赖变更后提交 `uv.lock`）
- 敏感信息一律走 `.env`（`.env` 不入 git）
- 接口出入参使用 Pydantic 模型校验，字段带中文 `description`（保持 /docs 可读）
- 数据库操作一律 ORM/参数化，禁止拼接 SQL；只连本地业务库
- 若接入 MySQL：绝不操作 `mysql`、`sys`、`performance_schema` 等系统库
- docstring 采用 Google 风格

## 3. 下一步计划（按优先级）
- [ ] 修复/补全 pytest 测试（根路由断言、service 断言、注册登录用例），跑通 `uv run pytest`（归属：用户模块）
- [ ] 后端代码提交：按约定式提交 `feat(user): 实现用户注册、登录与当前用户接口`（待用户确认）
- [ ] 前端对接用户模块：注册/登录页面 + token 管理与请求拦截（归属：frontend-react）
- [ ] 长期：引入 Alembic 管理表结构迁移（归属：基础设施）

## 4. 相关文档
- 问答记录：docs/QA.md（已积累 Q1–Q18）
