# app/models/user.py —— user 表的 ORM 实体
# 作用：
# 1) 供 create_all 生成建表 SQL  
# 2) 之后所有增删查改都用这个类

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mysql_db import MysqlBase


class User(MysqlBase):
    """用户表实体，对应 MySQL 里的 user 表"""

    __tablename__ = "user"  # 告诉 SQLAlchemy：这个类对应哪张表

    # 写法说明：Mapped[类型] 声明"Python 属性 → 数据库列"。
    # mapped_column(第一个参数是"数据库真实列名"，你的表是驼峰命名，所以必须写清楚)

    # 主键：BIGINT 自增
    id: Mapped[int] = mapped_column("id", BigInteger, primary_key=True, autoincrement=True, comment="主键 id")

    # 登录账号：VARCHAR(256) 非空 + 唯一约束（对应你 SQL 里的 uk_userAccount 唯一键）
    user_account: Mapped[str] = mapped_column("userAccount", String(256), unique=True, comment="登录账号")

    # 密码：VARCHAR(512)，注意——里面存的是 bcrypt 哈希，不是明文
    user_password: Mapped[str] = mapped_column("userPassword", String(512), comment="密码(bcrypt哈希)")

    # 下面 3 列允许为空（可空字段的类型写成 str | None）
    user_name: Mapped[str | None] = mapped_column("userName", String(256), index=True, comment="用户昵称")
    user_avatar: Mapped[str | None] = mapped_column("userAvatar", String(1024), comment="用户头像URL")
    user_profile: Mapped[str | None] = mapped_column("userProfile", String(512), comment="用户简介")

    # 角色：非空 + 默认 'user'
    # server_default 表示"数据库层的默认值"，不赋值时数据库自动填 'user'
    user_role: Mapped[str] = mapped_column(
        "userRole", String(256), server_default=text("'user'"), comment="用户角色:user/admin"
    )

    # 三个时间列：默认当前时间；update_time 额外声明"更新时自动刷新"
    edit_time: Mapped[datetime] = mapped_column(
        "editTime", DateTime, server_default=text("CURRENT_TIMESTAMP"), comment="编辑时间"
    )
    create_time: Mapped[datetime] = mapped_column(
        "createTime", DateTime, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        "updateTime",
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),  # 对应你 SQL 里的 ON UPDATE CURRENT_TIMESTAMP
        comment="更新时间",
    )

    # 逻辑删除标记：默认 0（未删除）
    is_delete: Mapped[int] = mapped_column(
        "isDelete", SmallInteger, server_default=text("0"), comment="是否删除:0否 1是"
    )