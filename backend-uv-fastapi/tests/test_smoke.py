"""后端冒烟测试：验证 FastAPI 应用可导入、关键路由已登记（全程离线，不连数据库）。"""

from fastapi import FastAPI

from app.main import app


def test_app_created() -> None:
    """应用实例应成功创建。"""
    assert isinstance(app, FastAPI)


def test_openapi_has_expected_paths() -> None:
    """OpenAPI 里应登记本轮已交付的全部业务路由。

    这里断言的是"路由有没有挂上去"，而不是"接口返回什么值" ——
    后者需要数据库与模型，属于后续的接口级测试。
    """
    paths = set(app.openapi()["paths"].keys())
    expected = {
        "/api/user/register",
        "/api/user/login",
        "/api/user/current",
        "/api/generation/create",
        "/api/generation/list",
        "/api/generation/{task_uuid}",
        "/api/generation/{task_uuid}/preview-ticket",
        "/api/agent/chat",
        "/api/agent/session/{session_uuid}",
    }
    assert expected <= paths, f"缺少路由：{sorted(expected - paths)}"