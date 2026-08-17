"""全项目共享测试夹具：合法题库/作答/报告样例（契约校验测试的基础数据）"""
import pytest

from app.core.config import Settings
from app.core.sensitive import SensitiveFilter


@pytest.fixture
def settings():
    """默认测试配置（API Key 为占位值：测试全程 mock 模型，不发起真实请求）"""
    return Settings(deepseek_api_key="test-key")


@pytest.fixture
def sensitive():
    """黑名单为 ["赌博"] 的敏感词过滤器（服务层测试用）"""
    return SensitiveFilter(["赌博"])


@pytest.fixture
def test_app():
    """创建独立应用实例并注入独立任务存储（用例再按需注入 fake LLM/词表）"""
    from app.core.tasks import TaskStore
    from app.main import create_app

    app = create_app()
    app.state.store = TaskStore()
    return app


@pytest.fixture
async def client(test_app):
    """基于 test_app 的 httpx 测试客户端（ASGITransport 不经过网络）"""
    from httpx import ASGITransport, AsyncClient

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
