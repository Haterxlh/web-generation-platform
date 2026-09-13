# app/utils/utils_check/check_router.py —— 开发期脚本：检查路由是否正常
# 运行：uv run python -m app.utils.utils_check.check_router

import app.main as m


def show(routes, indent=0):
    for route in routes:
        path = getattr(route, "path", None)
        if path is None:
            # 新版 FastAPI 用 _IncludedRouter 包装 include_router() 进来的路由，
            # 必须调 effective_candidates() / effective_low_priority_routes() 才拿得到真正的路由
            show(route.effective_candidates(), indent)
            show(route.effective_low_priority_routes(), indent)
            continue
        methods = ",".join(sorted(route.methods)) if getattr(route, "methods", None) else "MOUNT"
        print(f"{'  ' * indent}{methods:12} {path}")


show(m.app.routes)