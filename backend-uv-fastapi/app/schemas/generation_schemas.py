# app/schemas/generation_schemas.py —— 生成模块的请求 / 响应模型
# 约定：请求体严格校验；响应体只暴露白名单字段（不含 is_delete 等内部字段）

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GenerateRequest(BaseModel):
    """发起一次生成的请求体。"""

    prompt: str = Field(min_length=2, max_length=2000, description="网页需求描述")

    # Literal 让非法值在进业务层之前就被挡掉（非法值会 422，错误信息自动带可选值）
    gen_type: Literal["single", "multi"] = Field(
        default="single",
        description="生成类型：single=单个 HTML 文件；multi=html+css+js 多文件",
    )


class GenerationTaskResponse(BaseModel):
    """生成任务详情（响应白名单）。"""

    # from_attributes：允许直接从 ORM 对象组装（同 user_schemas 的做法）
    model_config = ConfigDict(from_attributes=True)

    task_uuid: str = Field(description="任务唯一标识（目录名与接口路径都用它）")
    prompt: str = Field(description="用户需求原文")
    gen_type: str = Field(description="生成类型：single/multi")
    status: str = Field(description="状态：running/success/failed")

    result_dir: str | None = Field(default=None, description="产物相对目录")
    file_list: list[str] = Field(default_factory=list, description="产物文件名列表")
    preview_url: str | None = Field(default=None, description="网页预览地址（由业务层填充）")
    error_msg: str | None = Field(default=None, description="失败原因")
    duration_ms: int | None = Field(default=None, description="耗时(毫秒)")
    create_time: datetime | None = Field(default=None, description="创建时间")
    input_tokens: int | None = Field(default=None, description="输入token数")
    output_tokens: int | None = Field(default=None, description="输出token数(含思考)")
    reasoning_tokens: int | None = Field(default=None, description="其中思考token数")

    @field_validator("file_list", mode="before")
    @classmethod
    def _parse_file_list(cls, value: Any) -> list[str]:
        """ORM 里 fileList 存的是 JSON 字符串，进模型前转成 list。

        Args:
            value: ORM 属性值（JSON 字符串 / None）或已经是 list 的值。

        Returns:
            文件名列表；解析失败或为空时返回空列表（不抛异常，详情接口不该因为脏数据 500）。
        """
        if not value:
            return []
        if isinstance(value, str):
            try:
                data = json.loads(value)
            except json.JSONDecodeError:
                return []
            return data if isinstance(data, list) else []
        return value


class GenerationListResponse(BaseModel):
    """生成历史列表（分页）。"""

    total: int = Field(description="总条数")
    items: list[GenerationTaskResponse] = Field(default_factory=list, description="当前页数据")


class PreviewTicketResponse(BaseModel):
    """预览票据的签发结果。"""

    preview_url: str = Field(description="预览地址（前端直接在新标签页打开）")
    expires_in: int = Field(description="票据有效期（秒）")