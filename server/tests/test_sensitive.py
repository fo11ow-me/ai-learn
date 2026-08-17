"""敏感词过滤测试（方案文档 5.4：输入与生成结果双向过滤的底层能力）"""
from app.core.config import Settings
from app.core.sensitive import SensitiveFilter, load_filter


class TestSensitiveFilter:
    def test_contains_hit(self):
        f = SensitiveFilter(["赌博", "色情"])
        assert f.contains("我想学习赌博技巧") is True

    def test_contains_miss(self):
        f = SensitiveFilter(["赌博", "色情"])
        assert f.contains("光的波粒二象性是物理概念") is False

    def test_contains_hit_in_long_text(self):
        f = SensitiveFilter(["毒品"])
        assert f.contains("这是一段很长的正常文本，但中间提到了毒品相关内容。") is True


class TestLoadFilter:
    def test_load_builtin_words(self):
        f = load_filter(Settings(deepseek_api_key="", sensitive_words_file=""))
        assert f.contains("赌博") is True  # 内置词表命中
        assert f.contains("光的波粒二象性") is False

    def test_load_merge_env_file(self, tmp_path):
        extra = tmp_path / "extra.txt"
        extra.write_text("自定义词\n# 这是注释\n\n", encoding="utf-8")
        f = load_filter(Settings(deepseek_api_key="", sensitive_words_file=str(extra)))
        assert f.contains("自定义词") is True  # 扩展词表生效
        assert f.contains("赌博") is True  # 内置词表仍在（合并而非替换）
        assert f.contains("这是注释") is False  # 注释行不参与匹配

    def test_load_missing_env_file_ignored(self, tmp_path):
        f = load_filter(Settings(deepseek_api_key="", sensitive_words_file=str(tmp_path / "不存在.txt")))
        assert f.contains("赌博") is True  # 扩展文件缺失时仍可用内置词表
