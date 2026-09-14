"""生成模块响应模型的离线测试：验证 fileList 的 JSON 字符串会被解析成列表。"""

from app.schemas.generation_schemas import GenerationTaskResponse


class FakeTask:
    """模拟 ORM 对象：注意 file_list 存的是 JSON 字符串（数据库里就是这样）。"""

    task_uuid = "abc123"
    prompt = "做一个待办清单页面"
    gen_type = "single"
    status = "success"
    result_dir = "1/abc123"
    file_list = '["index.html", "style.css"]'
    error_msg = None
    duration_ms = 1000
    create_time = None
    input_tokens = 1
    output_tokens = 2
    reasoning_tokens = 3


def test_file_list_is_parsed_from_json_string() -> None:
    """JSON 字符串应被解析成字符串列表（前端类型断言 string[] 的前提）。"""
    model = GenerationTaskResponse.model_validate(FakeTask())
    assert model.file_list == ["index.html", "style.css"]


def test_file_list_falls_back_to_empty_list_on_dirty_data() -> None:
    """脏数据不应让详情接口 500，而是退化成空列表。"""

    class DirtyTask(FakeTask):
        file_list = "这不是 JSON"

    assert GenerationTaskResponse.model_validate(DirtyTask()).file_list == []