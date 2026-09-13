# app/utils/weg_gen/prompt_loader.py —— 读取 app/prompts/ 下的提示词文件
# 为什么不用 ChatPromptTemplate 装提示词：见文件末尾注释（花括号会炸模板）

from functools import lru_cache
from pathlib import Path

# 本文件在 app/utils/weg_gen/ 下：parents[0]=weg_gen, parents[1]=utils, parents[2]=app
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """按名称读取提示词文件（不含 .md 后缀）。

    读取结果会被缓存：提示词是随代码发布的静态资源，运行时不会变，
    没必要每个请求都读一次磁盘。

    Args:
        name: 提示词文件名（不含扩展名），如 "single_html_system"。

    Returns:
        提示词全文（已去掉首尾空白）。

    Raises:
        FileNotFoundError: 文件不存在；异常信息里会列出可用文件名，便于排查拼写错误。
    """
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        available = sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))
        raise FileNotFoundError(f"提示词 {path.name} 不存在，当前可用：{available}")

    # 必须显式指定 utf-8：Windows 中文环境下默认是 GBK，
    # 提示词里的 emoji / 特殊符号会直接抛 UnicodeDecodeError
    return path.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    # 测试：打印所有可用提示词文件名
    print(load_prompt("single_html_system"))
