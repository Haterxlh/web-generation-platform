# app/repositories/user_repository.py —— 数据访问层：user 表的"查存管家"
# 职责：只做数据库操作（CRUD），不写业务规则
# 为什么每个查询都带 is_delete == 0：
#   表用的是逻辑删除（isDelete 标记），带条件才能保证"已删除的数据查不出来"

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    """把 user 表的所有数据库操作集中在这里，方法名即语义"""

    @staticmethod
    def create(db: Session, user: User) -> User:
        """
        把新用户写进数据库并返回（带数据库生成的主键 id）
        
        Args:
            db (Session): 数据库会话
            user (User): 用户对象
        
        Returns:
            User: 用户对象（带 id）
        """
        db.add(user)       # 1. 加入会话（还没真正入库）
        db.commit()        # 2. 提交事务 —— 改数据后必须 commit，否则不生效
        db.refresh(user)   # 3. 重新从库里读一遍，让 id、默认值等数据库生成的值回填到对象
        return user

    @staticmethod
    def get_by_user_account(db: Session, user_account: str) -> User | None:
        """
        按账号查用户
        
        Args:
            db (Session): 数据库会话
            user_account (str): 用户账号，业务上保证 user_account 唯一
        
        Returns:
            User | None: 用户对象（如果有），否则 None
        """
        stmt = select(User).where(
            User.user_account == user_account,
            User.is_delete == 0,          # 只查未删除的
        )
        return db.scalar(stmt)            # 返回单条记录，没有则 None

    @staticmethod
    def get_by_id(db: Session, user_id: int) -> User | None:
        """
        按主键查用户
        
        Args:
            db (Session): 数据库会话
            user_id (int): 用户主键
        
        Returns:
            User | None: 用户对象（如果有），否则 None
        """
        stmt = select(User).where(
            User.id == user_id,
            User.is_delete == 0,
        )
        return db.scalar(stmt)