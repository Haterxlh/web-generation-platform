"""后端冒烟测试：验证 FastAPI 应用可正常导入与响应。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_app_created() -> None:
    """应用实例应成功创建。"""
    assert app is not None


def test_root_endpoint() -> None:
    """根路由应返回 Hello World。"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"Hello": "World"}
