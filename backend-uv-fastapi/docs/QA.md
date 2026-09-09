# QA.md —— 开发问答记录

> 本文档汇总 backend-uv-fastapi（FastAPI + uv + SQLAlchemy + MySQL）开发过程中的疑问与解答，
> 供新手复习使用。格式：每个问题带编号与 tag（主题标签），随后是对应简答。

---

### Q1: 我该如何连接本地的 MySQL？
**tag:** `mysql` | `配置` | `入门`

**A1:**

1. 添加依赖：`uv add sqlalchemy pymysql pydantic-settings`。
2. 在项目根目录创建 `.env`（已被 `.gitignore` 忽略，不进 git），写入连接信息：
   ```ini
   MYSQL_HOST=127.0.0.1
   MYSQL_PORT=3306
   MYSQL_USER=root
   MYSQL_PASSWORD=你的密码
   MYSQL_DB_NAME=wgp_db
   ```
3. 配置类（`app/core/mysql_config.py`）用 `pydantic-settings` 读取，并拼出连接串：
   `mysql+pymysql://用户:密码@127.0.0.1:3306/库名?charset=utf8mb4`。
4. 会话层（`app/core/mysql_db.py`）创建 `engine`、`sessionmaker`、`DeclarativeBase` 基类与
   `get_mysql_db()` 依赖。
5. 之后一律使用 ORM/参数化语句操作数据，禁止拼接 SQL 字符串；数据源只指向本地业务库，
   绝不碰 `mysql`、`sys`、`performance_schema` 等系统库。

> 连接用 `127.0.0.1` 而非 `localhost`，可避免 MySQL 把 `localhost` 当成本机 socket 连接导致连不上。

---

### Q2: 为什么要写 `def get_db(): ... yield db`，而不是直接 `return db`？
**tag:** `FastAPI` | `依赖注入` | `会话管理`

**A2:**

因为 FastAPI 的生命周期管理。`yield` 把函数变成了"上下文管理器"：

- 请求进来时：执行 `yield` 前面的代码（创建数据库会话 db），把 db 交给接口使用。
- 请求结束时：无论接口运行成功还是报错，都会回到 `yield` 后面的 `finally: db.close()`，
  保证数据库连接一定会被关闭。

如果直接 `return db`，连接就会一直挂着不释放，很快把数据库连接池占满卡死。

---

### Q3: `-> Generator[Session, None, None]` 里面 3 个参数分别是什么？
**tag:** `Python` | `类型标注` | `typing`

**A3:**

这是给 VS Code 和代码检查工具看的"使用说明书"，表示这个函数是个**只出不进**的生成器：

| 位置 | 含义 | 本项目中的值 |
|---|---|---|
| 第1个（Session） | 函数"吐出来"的东西是什么 | 数据库会话 `Session` |
| 第2个（None） | 外部能不能"塞进去"东西（高级用法） | 不能，不接受任何输入 |
| 第3个（None） | 函数最终结束时返回什么 | 什么都不返回，直接关闭 |

记口诀：**"吐出 Session，不收指令，不写总结"**。

---

### Q4: 接口里写 `db: Session = Depends(get_db)` 是什么意思？
**tag:** `FastAPI` | `依赖注入`

**A4:**

`Depends(get_db)` 告诉 FastAPI："每次执行这个接口之前，先去调用 `get_db()` 给我弄一个数据库会话出来。"

- FastAPI 会自动把 `get_db()` 里 `yield` 出来的 db 赋值给接口参数。
- 接口执行完后，FastAPI 自动回到 `get_db()` 的 `finally` 把连接关闭。
- 你完全不需要手动管理 `db.close()`，全自动。

---

### Q5: `autocommit=False` 和 `autoflush=False` 是干嘛的？
**tag:** `SQLAlchemy` | `事务`

**A5:**

- `autocommit=False`（关闭自动提交）：禁止"改一条数据就立刻写硬盘"。必须手动执行
  `db.commit()`，数据库才会真正写入。好处是：如果中间报错，可以 `db.rollback()`
  撤销所有操作，避免数据错乱（事务原子性）。
- `autoflush=False`（关闭自动刷新）：平时改数据只存在 Python 内存里，不会频繁发 SQL
  去骚扰数据库。等到 `commit()` 时，一次性把所有修改打包发给数据库，性能更高。

---

### Q6: `pool_pre_ping=True` 和 `pool_recycle=3600` 是必须的吗？
**tag:** `SQLAlchemy` | `连接池` | `MySQL`

**A6:**

强烈建议加上，能救你一命：

- `pool_pre_ping=True`：每次取连接前先试探一下数据库还活着没。MySQL 默认 8 小时没操作
  会自动断线，加上这个后如果断了会自动重连，防止网站半夜突然报错崩溃。
- `pool_recycle=3600`：每 1 小时主动换一根新连接，避免连接用太久被数据库强制回收，
  省得报 `MySQL server has gone away`。

---

### Q7: 能不能不写 SQL，直接用 Python 代码创建表？
**tag:** `SQLAlchemy` | `create_all` | `建表`

**A7:**

可以。用 `Base.metadata.create_all(engine)` 即可让 SQLAlchemy 根据 ORM 模型自动生成并执行建表 SQL。

两种路线对比：

| 路线 | 谁说了算 | 做法 |
|---|---|---|
| A：手动执行 SQL | 你的 SQL 文件 | 在 MySQL 里跑一次 `CREATE TABLE` |
| B：Python 建表 | ORM 模型 | `Base.metadata.create_all(bind=engine)` |

要点：
- `create_all` **只会创建数据库中不存在的表**，已存在的表一律不动，可安全重复运行。
- 建出来的表 = 模型里声明的内容。模型没声明的东西（唯一约束、索引、默认值、注释）
  不会被建出来，所以要让模型完整表达设计。
- 局限：**不能修改已存在的表**。以后模型要加列/改类型时，`create_all` 帮不上忙，
  需要学 Alembic 迁移工具。

---

### Q8: `create_all` 之前为什么必须 `import app.models.user`？
**tag:** `SQLAlchemy` | `metadata` | `新手坑`

**A8:**

`create_all` 只创建"已注册到 `Base.metadata` 的模型"。模型文件被 import 的那一刻，
`class User(Base)` 才会把自己登记进 metadata；没 import 过，metadata 就是空的，
`create_all` 无事可做（不会报错，但一张表也建不出来）。

```python
import app.models.user  # noqa: F401   ← 必须有一行，注册模型
Base.metadata.create_all(bind=engine)
```

以后新增模型文件，也要在脚本里加一行对应的 import。

---

### Q9: NOT NULL 且有默认值的列，为什么 ORM 里还要声明 `server_default`？
**tag:** `SQLAlchemy` | `默认值` | `新手坑`

**A9:**

用实验验证过的结论：如果模型里**不声明** `server_default`，插入时不给该列赋值，
SQLAlchemy 会把 `NULL` 显式写进 SQL → 触发 `NOT NULL constraint failed` 报错；
声明 `server_default` 后，SQLAlchemy 知道该列由数据库填默认值，会**省略该列**，
数据库自动填入默认值（如 `user`、`CURRENT_TIMESTAMP`、`0`）。

示例：
```python
user_role: Mapped[str] = mapped_column(
    "userRole", String(256), server_default=text("'user'"), comment="角色"
)
```

> 该坑与"手动 SQL 建表还是 create_all 建表"无关，两条路都必须声明。

---

### Q10: Repository 里为什么没有 `from_attributes`？
**tag:** `Pydantic` | `分层` | `Schema`

**A10:**

因为 `from_attributes=True` 属于**响应模型（schemas）**的配置，不属于 Repository：

- Repository 的职责：从数据库取行，返回 **ORM 对象**（`User` 实例）。
- Schema 的职责：决定"怎么把对象变成 JSON"。`from_attributes=True` 表示允许 Pydantic
  从 ORM 对象上按属性名取值来组装响应（`UserResponse` 不写密码字段，天然过滤）。

流转：MySQL 行 → Repository 返回 `User` 对象 → 构造 `LoginResponse(user=user)` →
Pydantic 依据 `UserResponse.model_config = ConfigDict(from_attributes=True)` 取属性 → JSON。

---

### Q11: 表列是驼峰命名（`userAccount`），ORM 里怎么写？
**tag:** `SQLAlchemy` | `列映射`

**A11:**

Python 属性用 snake_case，`mapped_column` 的第一个参数显式写数据库真实列名：

```python
user_account: Mapped[str] = mapped_column("userAccount", String(256), ...)
```

否则 SQLAlchemy 默认找 `user_account` 这种下划线列名，会报"找不到列"。

同理，逻辑删除表的所有查询都要带 `is_delete == 0` 过滤条件，否则会把已删除数据查出来。

---

### Q12: backend-uv-fastapi 目录结构应该怎么组织？和 Spring Boot 一样吗？
**tag:** `架构` | `分层`

**A12:**

推荐分层：`api/`（路由层）、`services/`（业务层）、`repositories/`（数据访问层）、
`models/`（ORM 实体 + Schema）、`core/`（配置/会话/依赖注入）、`utils/`（通用工具）。

与 Spring Boot 完全对应：**Router = Controller，Service = Service，Repository = DAO**。
关键区别：FastAPI 的 `models/` 下会区分 ORM 实体（数据库表）与 Pydantic Schema（校验/DTO），
比 Java 更强调"双模型"隔离。

调用方向固定：`api → services → repositories → 数据库`，禁止跨层调用（如路由直接写 SQL）。

---

### Q13: 运行脚本报 `ModuleNotFoundError: No module named 'app'` 怎么办？
**tag:** `Python` | `导入` | `报错排查`

**A13:**

不要用 `python app/xxx.py` 直接运行脚本（此时包根不对，找不到 `app`）。
应在项目根目录使用模块方式运行：

```bash
python -m app.utils.create_all_table
# 或
uv run python -m app.utils.xxx
```

测试文件同理使用**绝对导入**（`from app.schemas...`），因为 `tests` 和 `app` 是同级顶级包。

---

### Q14: Pydantic 报 `Extra inputs are not permitted`（如 mysql_host 等）怎么办？
**tag:** `pydantic-settings` | `配置` | `报错排查`

**A14:**

原因：某个 Settings 类（如 `JwtSettings`）没声明 `mysql_*` 字段，但 `.env` 或系统环境变量
里有这些变量，而 Pydantic v2 默认 `extra="forbidden"`（多余即报错）。

解决：在该 Settings 类的 `model_config` 中加 `extra="ignore"`，忽略未定义的额外字段：

```python
model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")
```

这样一份 `.env` 被多个 Settings 类共享时，各取所需、互不干扰。

---

### Q15: `db.scalar()` 里的 "scalar" 是什么意思？写法对吗？
**tag:** `SQLAlchemy` | `查询`

**A15:**

"scalar" 指"标量"（单一值）。`db.scalar(stmt)` 执行查询并返回结果集第一行第一列的单个值；
如果查询的是 `select(User)`，则返回 `User` 对象实例。

`db.scalar(select(User).where(...))` 写法正确：匹配一条返回 `User` 对象，没有则返回 `None`。
潜在风险：若意外匹配多条，会**静默取第一条**。更严谨的写法是 `.first()`
或 `.scalar_one_or_none()`（多条时抛异常，便于暴露数据问题）。

---

### Q16: pytest 常见问题：相对导入报错 / collected 0 items / 看不到 print 输出
**tag:** `pytest` | `测试` | `报错排查`

**A16:**

- 相对导入报错：tests 与 app 是同级顶级包，测试文件里要用**绝对导入**
  （`from app.schemas...`），不要用 `from ..app.schemas...`，并通过 `python -m` 或 `pytest` 运行。
- `collected 0 items`：文件中没有定义以 `test_` 开头的函数。Pytest 只识别
  `test_` 前缀的函数或 `Test` 前缀的类。
- 测试通过了但没看到 `print()` 输出：Pytest 默认捕获标准输出，运行时加 `-s`
  或 `--capture=no` 即可显示。

---

### Q17: `model_validate` 是什么？`create_time` 报 "Input should be a valid datetime" 怎么办？
**tag:** `Pydantic v2` | `Schema` | `报错排查`

**A17:**

`model_validate` 是 Pydantic v2 的万能解析器：把字典、ORM 对象（需配置
`from_attributes=True`）或类实例转换为 Pydantic 模型。它统一了旧版的
`parse_obj` 和 `from_orm`。

`create_time` 报 "Input should be a valid datetime" 且传入的是 `None` 的原因：
Schema 中字段类型是 `datetime` 且**必填**（即使写了 `Optional` 但没给默认值）。
修改为 `create_time: Optional[datetime] = None`（加 `= None`）使其变为可选字段。

---

### Q18: ORM 的全称与含义是什么？
**tag:** `概念` | `ORM`

**A18:**

- 英文：Object-Relational Mapping
- 中文：对象关系映射

简单来说，它是一种程序设计技术，用于在面向对象的编程语言（如 Java、Python、C#）和
关系型数据库（如 MySQL、PostgreSQL）之间建立"桥梁"：把数据库表映射成类、行映射成对象，
让开发者用操作对象的方式操作数据库，而不用手写 SQL。
