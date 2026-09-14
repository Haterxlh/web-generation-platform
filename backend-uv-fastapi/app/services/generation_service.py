# app/services/generation_service.py —— 业务层：生成任务的"规则中枢"
# 职责：建任务 → 调 agents 生成 → 落盘 → 落库（状态流转）
# 不写 SQL（交 repository）、不写 prompt（交 agents）、不碰 HTTP（交 api）
# 调用链（设计约定 §3.3）：api → 本层 → {repositories, agents, utils}

import json
import time
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.single_html_flow import generate_single_html
from app.agents.multi_file_graph import generate_multi_file
from app.core.storage_config import storage_settings
from app.models.generation_task import GenerationTask
from app.repositories.generation_repository import GenerationTaskRepository
from app.schemas.generation_schemas import (
    GenerateRequest,
    GenerationListResponse,
    GenerationTaskResponse,
)
from app.utils.weg_gen.file_writer import write_files
from app.agents.common import GenerationFailedError

# 生成类型 → agents 层函数。
# 步骤 8 只要在这里加一行 "multi": generate_multi_file，本文件其它地方一行都不用动
_GENERATORS = {
    "single": generate_single_html,
    "multi": generate_multi_file,
}


class GenerationService:
    """生成相关的业务操作集合。"""

    @staticmethod
    def create(db: Session, user_id: int, req: GenerateRequest) -> GenerationTaskResponse:
        """创建并执行一次生成。

        流程：建任务(status=running) → 调 agents 生成 → 落盘 → 更新为 success / failed。

        Args:
            db: 数据库会话（由 FastAPI 依赖注入）。
            user_id: 当前登录用户 id。
            req: 生成请求（需求 + 类型）。

        Returns:
            生成任务详情（含产物目录、文件名清单、预览地址）。

        Raises:
            HTTPException: 未实现的生成类型返回 501；生成失败返回 500（任务记录会留下 failed 与原因）。
        """
        generator = _GENERATORS.get(req.gen_type)
        if generator is None:
            raise HTTPException(status_code=501, detail=f"生成类型 {req.gen_type} 尚未实现")

        # 1) 先建任务：task_uuid 在内存里生成，目录名因此不依赖数据库自增 id（设计约定 §4）
        task = GenerationTask(
            task_uuid=uuid.uuid4().hex,
            user_id=user_id,
            prompt=req.prompt,
            gen_type=req.gen_type,
            status="running",
        )
        task = GenerationTaskRepository.create(db, task)

        # 2) 生成 → 落盘 → 更新任务
        started = time.perf_counter()
        try:
            result = generator(req.prompt)
            task.result_dir = write_files(user_id, task.task_uuid, result.files)
            task.file_list = json.dumps(sorted(result.files), ensure_ascii=False)
            task.status = "success"
            task.input_tokens = result.usage.input_tokens
            task.output_tokens = result.usage.output_tokens
            task.reasoning_tokens = result.usage.reasoning_tokens
            task.duration_ms = int((time.perf_counter() - started) * 1000)
        except GenerationFailedError as error:
            # 可预期的失败：异常上带着用量，一起记下来
            task.status = "failed"
            task.error_msg = str(error)[:1000]
            task.input_tokens = error.usage.input_tokens
            task.output_tokens = error.usage.output_tokens
            task.reasoning_tokens = error.usage.reasoning_tokens
            task.duration_ms = int((time.perf_counter() - started) * 1000)
            GenerationTaskRepository.update(db, task)
            raise HTTPException(status_code=500, detail=f"生成失败：{task.error_msg}") from error
        except Exception as error:
            # 意外失败（网络、鉴权…）：通常没产生 token，用量留空
            task.status = "failed"
            task.error_msg = str(error)[:1000]
            task.duration_ms = int((time.perf_counter() - started) * 1000)
            GenerationTaskRepository.update(db, task)
            raise HTTPException(status_code=500, detail=f"生成失败：{task.error_msg}") from error

        # 成功路径：把 status/result_dir/file_list/用量 一次性提交入库，再把结果交给接口层
        # 成功路径提交 + 返回（缺少这两行会导致 500 ResponseValidationError）
        GenerationTaskRepository.update(db, task)
        return GenerationService._to_response(task)

    @staticmethod
    def get_task(db: Session, user_id: int, task_uuid: str) -> GenerationTaskResponse:
        """查单个任务详情（只能查自己的）。

        Args:
            db: 数据库会话。
            user_id: 当前登录用户 id。
            task_uuid: 任务唯一标识。

        Returns:
            任务详情。

        Raises:
            HTTPException: 任务不存在或不属于当前用户，一律 404。
        """
        task = GenerationTaskRepository.get_by_task_uuid(db, task_uuid)
        # "不存在"和"不是你的"故意返回同一个结果：不泄露"这个 uuid 存在但不属于你"
        if task is None or task.user_id != user_id:
            raise HTTPException(status_code=404, detail="任务不存在")
        return GenerationService._to_response(task)

    @staticmethod
    def list_tasks(
        db: Session, user_id: int, page: int = 1, page_size: int = 20
    ) -> GenerationListResponse:
        """分页查"我的生成历史"。

        Args:
            db: 数据库会话。
            user_id: 当前登录用户 id。
            page: 页码，从 1 开始。
            page_size: 每页条数。

        Returns:
            总数 + 当前页数据。
        """
        total = GenerationTaskRepository.count_by_user(db, user_id)
        tasks = GenerationTaskRepository.list_by_user(
            db, user_id, offset=(page - 1) * page_size, limit=page_size
        )
        return GenerationListResponse(
            total=total,
            items=[GenerationService._to_response(task) for task in tasks],
        )

    @staticmethod
    def _to_response(task: GenerationTask) -> GenerationTaskResponse:
        """ORM 对象 → 响应模型，并补上派生字段 preview_url。

        Args:
            task: 任务 ORM 对象。

        Returns:
            响应模型。
        """
        response = GenerationTaskResponse.model_validate(task)
        if task.result_dir:
            # 预览 URL 的拼法只在后端这一处定义，前端不重复实现
            response.preview_url = f"{storage_settings.preview_prefix}/{task.result_dir}/index.html"
        return response