"""用户模块的离线单元测试：只碰 Pydantic 模型，不连数据库。"""

from app.schemas.user_schemas import UserResponse


def test_user_response_hides_password() -> None:
    """响应白名单里绝不能出现密码哈希与内部标记。"""
    assert "user_password" not in UserResponse.model_fields
    assert "is_delete" not in UserResponse.model_fields


def test_user_response_accepts_orm_like_object() -> None:
    """from_attributes=True：应能直接从"带同名属性的对象"组装。"""

    class FakeUser:
        id = 1
        user_account = "alice"
        user_name = None
        user_avatar = None
        user_profile = None
        user_role = "user"
        create_time = None

    model = UserResponse.model_validate(FakeUser())
    assert model.id == 1
    assert model.user_account == "alice"