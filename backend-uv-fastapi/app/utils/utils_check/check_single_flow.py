# app/utils/utils_check/check_single_flow.py —— 开发期脚本：真跑一次"单文件模式"完整链路
# 运行：uv run python -m app.utils.utils_check.check_single_flow
# 注意：会真实调用 API（花 token），所以不属于 pytest 用例

from app.core.mysql_db import MysqlSessionLocal
from app.repositories.generation_repository import GenerationTaskRepository
from app.schemas.generation_schemas import GenerateRequest
from app.services.generation_service import GenerationService
from app.utils.weg_gen.file_writer import task_dir

# 换一句话就能试别的需求
PROMPT = "做一个深色风格的「番茄钟」单页：25 分钟倒计时圆环，开始 / 暂停 / 重置三个按钮"
USER_ID = 1  # 用你库里真实存在的用户 id；表没建外键，写错也不报错，但历史列表会查不到


def main() -> None:
    """跑一次完整链路：建任务 → 生成 → 落盘 → 落库 → 回读。"""
    db = MysqlSessionLocal()
    try:
        response = GenerationService.create(
            db,
            user_id=USER_ID,
            req=GenerateRequest(prompt=PROMPT, gen_type="single"),
        )
        print("task_uuid :", response.task_uuid)
        print("status    :", response.status)
        print("file_list :", response.file_list)
        print("result_dir:", response.result_dir)
        print("duration  :", response.duration_ms, "ms")

        row = GenerationTaskRepository.get_by_task_uuid(db, response.task_uuid)
        print("DB 回读   :", row.status, "|", row.file_list, "|", row.duration_ms, "ms")

        print("产物文件  :", task_dir(USER_ID, response.task_uuid) / "index.html")
    finally:
        db.close()


if __name__ == "__main__":
    main()