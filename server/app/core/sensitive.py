"""敏感词过滤（方案文档 5.4 MVP 轻量方案：黑名单词表子串匹配；P1 升级第三方审核 API 时只改此模块）"""
from pathlib import Path

from app.core.config import Settings

_BUILTIN_WORDS_FILE = Path(__file__).resolve().parent / "sensitive_words.txt"


class SensitiveFilter:
    """黑名单词表过滤器：命中任一敏感词即返回 True"""

    def __init__(self, words: list[str]):
        self._words = [w for w in words if w]

    def contains(self, text: str) -> bool:
        """文本中是否包含任一敏感词（子串匹配）"""
        return any(word in text for word in self._words)


def load_filter(settings: Settings) -> SensitiveFilter:
    """加载内置词表 + 可选 env 扩展词表文件（每行一词，# 开头为注释），合并为一份过滤规则"""
    words = _read_words(_BUILTIN_WORDS_FILE)
    if settings.sensitive_words_file:
        extra = Path(settings.sensitive_words_file)
        if extra.is_file():
            words += _read_words(extra)
    return SensitiveFilter(words)


def _read_words(path: Path) -> list[str]:
    """读取词表文件：跳过空行与 # 注释行"""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
