"""接口数据契约（方案文档 4.2/4.3 单一来源）：同时作为 LLM 结构化输出 schema 与 API 请求校验"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

JUDGE_OPTIONS = ["正确", "错误"]
TOTAL_QUESTIONS = 5
TYPE_DISTRIBUTION = {"single": 2, "multiple": 1, "judge": 2}
MAX_CONTENT_LENGTH = 2000  # 方案文档 4.1：出题内容上限（超限 422）


class QuestionSchema(BaseModel):
    """单题契约（方案文档 4.2）：id 1-5 递增；judge 选项固定；answer 为正确选项索引数组"""

    id: int = Field(ge=1, le=TOTAL_QUESTIONS)
    type: Literal["single", "multiple", "judge"]
    question: str = Field(min_length=1)
    options: list[str]
    answer: list[int]
    explanation: str = Field(min_length=1)
    knowledge_point: str = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _check_answer_consistency(self) -> "QuestionSchema":
        if not self.answer:
            raise ValueError("answer 不能为空")
        if len(set(self.answer)) != len(self.answer):
            raise ValueError("answer 索引不能重复")
        if any(i < 0 or i >= len(self.options) for i in self.answer):
            raise ValueError("answer 索引越界")

        if self.type == "judge":
            if self.options != JUDGE_OPTIONS:
                raise ValueError("判断题 options 必须固定为 ['正确', '错误']")
            if len(self.answer) != 1:
                raise ValueError("判断题 answer 必须为单一索引")
        elif self.type == "single":
            if len(self.options) != 4:
                raise ValueError("单选题必须有 4 个选项")
            if len(self.answer) != 1:
                raise ValueError("单选题 answer 必须为单一索引")
        else:  # multiple
            if len(self.options) != 4:
                raise ValueError("多选题必须有 4 个选项")
            if len(self.answer) < 2:
                raise ValueError("多选题 answer 至少 2 个索引")
        return self


class QuizSchema(BaseModel):
    """题库契约（方案文档 4.2）：5 题 = 2 单选 + 1 多选 + 2 判断"""

    topic: str = Field(min_length=1, description="学习主题名（报告页标题）")
    source_summary: str = Field(min_length=1, description="AI 整理的知识摘要（供报告生成，不直接展示）")
    questions: list[QuestionSchema] = Field(min_length=TOTAL_QUESTIONS, max_length=TOTAL_QUESTIONS)

    @model_validator(mode="after")
    def _check_structure(self) -> "QuizSchema":
        ids = sorted(q.id for q in self.questions)
        if ids != list(range(1, TOTAL_QUESTIONS + 1)):
            raise ValueError("题目 id 必须为 1-5 递增且不重复")
        counts: dict[str, int] = {}
        for q in self.questions:
            counts[q.type] = counts.get(q.type, 0) + 1
        if counts != TYPE_DISTRIBUTION:
            raise ValueError("题型分布必须为 2 单选 + 1 多选 + 2 判断")
        return self


class AnswerRecord(BaseModel):
    """作答记录：题目 id + 已选选项索引数组"""

    question_id: int = Field(ge=1, le=TOTAL_QUESTIONS)
    selected: list[int] = Field(min_length=1)


class ReportRequest(BaseModel):
    """报告生成请求（方案文档 4.1 + 用户系统：可选 session_id 关联闯关记录）"""

    quiz: QuizSchema
    answers: list[AnswerRecord] = Field(min_length=TOTAL_QUESTIONS, max_length=TOTAL_QUESTIONS)
    session_id: int | None = None  # 可选：报告完成后回写对应 quiz_sessions.report_json

    @model_validator(mode="after")
    def _check_answers(self) -> "ReportRequest":
        if {a.question_id for a in self.answers} != {q.id for q in self.quiz.questions}:
            raise ValueError("作答必须覆盖全部题目")
        by_id = {q.id: q for q in self.quiz.questions}
        for a in self.answers:
            q = by_id[a.question_id]
            if any(i < 0 or i >= len(q.options) for i in a.selected):
                raise ValueError("作答索引越界")
            if q.type in ("single", "judge") and len(a.selected) != 1:
                raise ValueError("单选/判断题必须且只能选择一个选项")
        return self


class MasteryItem(BaseModel):
    """逐知识点掌握度（方案文档 4.3）"""

    knowledge_point: str = Field(min_length=1)
    level: int = Field(ge=0, le=100)
    comment: str = Field(min_length=1)


class AIReportSchema(BaseModel):
    """AI 生成报告部分（with_structured_output 输出契约）。
    正确率等统计字段由代码计算（WHY：正确率必须确定性计算，不能信任 AI 算术）"""

    summary: str = Field(min_length=1, description="知识总结 200~300 字（长度由 Prompt 约束）")
    mastery: list[MasteryItem] = Field(min_length=1)
    suggestions: list[str] = Field(min_length=2, max_length=3)
    quote: str = Field(min_length=1, max_length=30, description="学习金句，用于海报")


class QuizCreateRequest(BaseModel):
    """出题请求（方案文档 4.1 + RAG D4：content 必填，knowledge_base_id 可选）。
    指定 knowledge_base_id → 严格模式（仅基于该库出题，永不联网）；不指定 → 知识库优先 + 判定 + 缺口联网补缺"""

    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)
    knowledge_base_id: int | None = None  # 知识库 id（严格模式；归属校验在路由层）


class KnowledgeJudgeResult(BaseModel):
    """知识充分性判定契约（D4：LLM 判断知识库片段是否足以覆盖出题）。
    enough=false 时 missing_topics 供检索计划定向联网补缺"""

    enough: bool
    reason: str = Field(min_length=1, max_length=200)
    missing_topics: list[str] = Field(default=[], max_length=3)


class SearchPlanSchema(BaseModel):
    """联网检索计划（D3：LLM 单次结构化输出，代码按计划执行——决策与执行分离）。
    mode=search 时必须有 1~3 个关键词；mode=extract 时必须有 url，互斥校验"""

    mode: Literal["search", "extract"]
    keywords: list[str] = Field(default=[], description="搜索关键词（mode=search 时 1~3 个，每词 ≤30 字）")
    url: str = Field(default="", description="目标网页地址（mode=extract 时必填）")
    count: int = Field(default=5, ge=3, le=8, description="搜索返回条数")
    depth: Literal["basic", "advanced"] = "basic"

    @model_validator(mode="after")
    def _check_consistency(self) -> "SearchPlanSchema":
        if self.mode == "search":
            if not (1 <= len(self.keywords) <= 3):
                raise ValueError("search 模式必须有 1~3 个关键词")
            if any(len(kw) > 30 for kw in self.keywords):
                raise ValueError("关键词不能超过 30 字")
            if self.url:
                raise ValueError("search 模式不应提供 url")
        else:
            if not self.url:
                raise ValueError("extract 模式必须提供 url")
            if self.keywords:
                raise ValueError("extract 模式不应提供关键词")
        return self


class KnowledgeBaseCreate(BaseModel):
    """创建知识库请求（RAG：名称同用户下唯一）"""

    name: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=200)


class KnowledgeBaseUpdate(BaseModel):
    """重命名/改描述知识库请求（至少一项必填，路由层校验）"""

    name: str | None = Field(default=None, min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=200)


class ReportSchema(BaseModel):
    """报告契约（方案文档 4.3）= 代码计算的正确率统计 + AI 生成的文本部分（服务层组装）"""

    correct_rate: int = Field(ge=0, le=100)
    correct_count: int
    total_questions: int
    summary: str
    mastery: list[MasteryItem]
    suggestions: list[str]
    quote: str
