"""全项目共享测试夹具：合法题库/作答/报告样例 + 独立应用实例（含 SQLite 内存库）"""
import pytest

from app.core.config import Settings
from app.core.sensitive import SensitiveFilter


@pytest.fixture
def settings():
    """默认测试配置（API Key/JWT 密钥为测试值；AUTH_MOCK 默认开，不发起真实微信/LLM 请求）"""
    return Settings(deepseek_api_key="test-key", jwt_secret="test-secret")


@pytest.fixture
def sensitive():
    """黑名单为 ["赌博"] 的敏感词过滤器（服务层测试用）"""
    return SensitiveFilter(["赌博"])


class FakeSearchClient:
    """默认返回空资料（= 降级）的搜索客户端。

    API 级测试不依赖真实 TAVILY_API_KEY 与网络；用例可按需注入 search_result/
    extract_result 或抛错，并断言调用记录。
    """

    def __init__(self, search_result="", extract_result="", search_error=None, extract_error=None):
        self.search_result = search_result
        self.extract_result = extract_result
        self.search_error = search_error
        self.extract_error = extract_error
        self.search_calls: list[tuple] = []
        self.extract_calls: list[str] = []

    async def search(self, query, count, depth):
        self.search_calls.append((query, count, depth))
        if self.search_error:
            raise self.search_error
        return self.search_result

    async def extract(self, url):
        self.extract_calls.append(url)
        if self.extract_error:
            raise self.extract_error
        return self.extract_result


@pytest.fixture
def test_app():
    """创建独立应用实例并注入独立任务存储与 SQLite 内存库（用例再按需注入 fake LLM/词表）"""
    from sqlalchemy.pool import StaticPool

    from app.core.config import Settings
    from app.core.db import DBEngine
    from app.core.tasks import TaskStore
    from app.main import create_app
    from app.services.knowledge_base import KnowledgeBaseService

    app = create_app()
    app.state.settings = Settings(deepseek_api_key="test-key", embedding_api_key="test-embed-key",
                                  jwt_secret="test-secret")
    app.state.store = TaskStore()
    app.state.db = DBEngine()
    app.state.db.bind("sqlite+aiosqlite://", poolclass=StaticPool)
    app.state.search = FakeSearchClient()
    # 内存 Chroma + 确定性伪 embedding：上传→向量化→轮询全流程真实跑通，不依赖百炼 key 与网络
    app.state.knowledge_base = KnowledgeBaseService(
        app.state.settings, build_embeddings=lambda s: FakeEmbeddings()
    )
    return app


@pytest.fixture
async def client(test_app):
    """基于 test_app 的 httpx 测试客户端（ASGITransport 不经过网络；每用例先建表）"""
    from httpx import ASGITransport, AsyncClient

    await test_app.state.db.create_all()
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def make_valid_quiz():
    """构造合法题库 dict（2 单选 + 1 多选 + 2 判断，符合方案文档 4.2 契约）"""

    def _make() -> dict:
        return {
            "topic": "光的波粒二象性",
            "source_summary": "光既表现出波动性（干涉、衍射）又表现出粒子性（光电效应），二者互补统一。",
            "questions": [
                {
                    "id": 1,
                    "type": "single",
                    "question": "光在传播过程中表现出干涉和衍射现象，这说明光具有什么性质？",
                    "options": ["粒子性", "波动性", "同时具有波动性和粒子性", "既无波动性也无粒子性"],
                    "answer": [1],
                    "explanation": "干涉与衍射是波动特有的现象，因此说明光具有波动性。",
                    "knowledge_point": "光的本性",
                },
                {
                    "id": 2,
                    "type": "single",
                    "question": "双缝干涉实验中，明条纹处两列光波满足什么条件？",
                    "options": ["光程差为零", "光程差为波长的整数倍", "光程差为半波长的奇数倍", "与光程差无关"],
                    "answer": [1],
                    "explanation": "干涉相长出现在光程差为波长整数倍的位置。",
                    "knowledge_point": "光的干涉",
                },
                {
                    "id": 3,
                    "type": "multiple",
                    "question": "下列现象中，能说明光具有粒子性的是？",
                    "options": ["光电效应", "光的干涉", "光的衍射", "康普顿效应"],
                    "answer": [0, 3],
                    "explanation": "光电效应与康普顿效应只能用量子化解释，体现粒子性；干涉衍射体现波动性。",
                    "knowledge_point": "光的本性",
                },
                {
                    "id": 4,
                    "type": "judge",
                    "question": "光电效应实验中，光电子能否逸出只取决于光的强度，与频率无关。",
                    "options": ["正确", "错误"],
                    "answer": [1],
                    "explanation": "能否逸出取决于入射光频率是否高于截止频率，强度只影响逸出电子数量。",
                    "knowledge_point": "光电效应",
                },
                {
                    "id": 5,
                    "type": "judge",
                    "question": "光的波粒二象性意味着光同时表现出波动性和粒子性，二者随观察方式互补。",
                    "options": ["正确", "错误"],
                    "answer": [0],
                    "explanation": "波粒二象性是量子力学的基本结论，波动性与粒子性互补而非矛盾。",
                    "knowledge_point": "光的本性",
                },
            ],
        }

    return _make


def make_valid_answers() -> list[dict]:
    """全部答对的作答记录（question_id 对齐题目 id，索引与题目 answer 一致）"""
    return [
        {"question_id": 1, "selected": [1]},
        {"question_id": 2, "selected": [1]},
        {"question_id": 3, "selected": [0, 3]},
        {"question_id": 4, "selected": [1]},
        {"question_id": 5, "selected": [0]},
    ]


class FakeEmbeddings:
    """确定性伪 embedding：服务级测试不依赖真实百炼 key 与网络。

    相同文本 → 相同向量（余弦≈1 命中），不同文本 → 伪随机正交（被阈值过滤）。
    """

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.calls: list[str] = []

    def embed_documents(self, texts):
        self.calls.extend(texts)
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        self.calls.append(text)
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        import hashlib

        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [float(byte) / 255.0 - 0.5 for byte in digest]
        vec = (vec * (self.dim // 32 + 1))[: self.dim]
        norm = sum(v * v for v in vec) ** 0.5
        return [v / norm for v in vec]


class FakeKnowledgeBase:
    """可编程的知识库服务替身（出题流水线测试用）：按指定行为返回命中/抛错，记录调用"""

    def __init__(self, hits=None, error=None):
        self.hits = hits or []
        self.error = error
        self.search_calls: list[tuple] = []

    async def search(self, user_id, query, kb_id=None, **kwargs):
        self.search_calls.append((user_id, query, kb_id))
        if self.error:
            raise self.error
        return self.hits


def make_valid_ai_report() -> dict:
    """合法 AI 报告生成部分样例（方案文档 4.3 的 AI 生成字段）"""
    return {
        "summary": "本次学习了光的波粒二象性：干涉衍射体现波动性，光电效应体现粒子性。",
        "mastery": [
            {"knowledge_point": "光的本性", "level": 90, "comment": "掌握良好"},
            {"knowledge_point": "光电效应", "level": 60, "comment": "截止频率概念需巩固"},
        ],
        "suggestions": ["重温光电效应截止频率概念", "练习 3 道光电效应计算题"],
        "quote": "光既是波，也是粒子",
    }
