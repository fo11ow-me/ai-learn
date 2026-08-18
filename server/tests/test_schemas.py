"""Pydantic 契约校验测试（方案文档 4.2/4.3：坏数据必须在进入服务前被拒绝）"""
import pytest
from pydantic import ValidationError

from app.models.schemas import AIReportSchema, QuizSchema, ReportRequest, ReportSchema, SearchPlanSchema
from tests.conftest import make_valid_ai_report, make_valid_answers


class TestQuizSchema:
    def test_valid_quiz_passes(self, make_valid_quiz):
        quiz = QuizSchema.model_validate(make_valid_quiz())
        assert len(quiz.questions) == 5
        assert quiz.topic == "光的波粒二象性"

    def test_rejects_wrong_type_distribution(self, make_valid_quiz):
        data = make_valid_quiz()
        data["questions"][3]["type"] = "single"  # 变成 3 单选
        with pytest.raises(ValidationError):
            QuizSchema.model_validate(data)

    def test_rejects_judge_options_not_fixed(self, make_valid_quiz):
        data = make_valid_quiz()
        data["questions"][3]["options"] = ["对", "错"]
        with pytest.raises(ValidationError):
            QuizSchema.model_validate(data)

    def test_rejects_answer_index_out_of_range(self, make_valid_quiz):
        data = make_valid_quiz()
        data["questions"][0]["answer"] = [4]  # options 只有 0-3
        with pytest.raises(ValidationError):
            QuizSchema.model_validate(data)

    def test_rejects_multiple_single_answer(self, make_valid_quiz):
        data = make_valid_quiz()
        data["questions"][2]["answer"] = [0]  # 多选只有一个索引
        with pytest.raises(ValidationError):
            QuizSchema.model_validate(data)

    def test_rejects_knowledge_point_too_long(self, make_valid_quiz):
        data = make_valid_quiz()
        data["questions"][0]["knowledge_point"] = "这是一个超过十个字的知识点标签呀"
        with pytest.raises(ValidationError):
            QuizSchema.model_validate(data)

    def test_rejects_four_questions(self, make_valid_quiz):
        data = make_valid_quiz()
        data["questions"] = data["questions"][:4]
        with pytest.raises(ValidationError):
            QuizSchema.model_validate(data)

    def test_rejects_duplicate_ids(self, make_valid_quiz):
        data = make_valid_quiz()
        data["questions"][1]["id"] = 1
        with pytest.raises(ValidationError):
            QuizSchema.model_validate(data)


class TestReportRequest:
    def test_valid_report_request_passes(self, make_valid_quiz):
        req = ReportRequest.model_validate({"quiz": make_valid_quiz(), "answers": make_valid_answers()})
        assert len(req.answers) == 5

    def test_rejects_missing_answer(self, make_valid_quiz):
        with pytest.raises(ValidationError):
            ReportRequest.model_validate({"quiz": make_valid_quiz(), "answers": make_valid_answers()[:4]})

    def test_rejects_invalid_index(self, make_valid_quiz):
        answers = make_valid_answers()
        answers[0]["selected"] = [9]
        with pytest.raises(ValidationError):
            ReportRequest.model_validate({"quiz": make_valid_quiz(), "answers": answers})

    def test_rejects_multi_selected_on_single(self, make_valid_quiz):
        answers = make_valid_answers()
        answers[0]["selected"] = [0, 1]  # 单选选了 2 个
        with pytest.raises(ValidationError):
            ReportRequest.model_validate({"quiz": make_valid_quiz(), "answers": answers})

    def test_rejects_unknown_question_id(self, make_valid_quiz):
        answers = make_valid_answers()
        answers[4]["question_id"] = 6
        with pytest.raises(ValidationError):
            ReportRequest.model_validate({"quiz": make_valid_quiz(), "answers": answers})

    def test_rejects_empty_selected(self, make_valid_quiz):
        answers = make_valid_answers()
        answers[0]["selected"] = []
        with pytest.raises(ValidationError):
            ReportRequest.model_validate({"quiz": make_valid_quiz(), "answers": answers})


class TestAIReportSchema:
    def test_valid_ai_report_passes(self):
        report = AIReportSchema.model_validate(make_valid_ai_report())
        assert len(report.suggestions) == 2

    def test_rejects_quote_too_long(self):
        data = make_valid_ai_report()
        data["quote"] = "这是一句远远超过三十个字的金句啊一二三四五六七八九十一二三四五"  # 31 字
        with pytest.raises(ValidationError):
            AIReportSchema.model_validate(data)

    def test_rejects_too_few_suggestions(self):
        data = make_valid_ai_report()
        data["suggestions"] = ["只有一条建议"]
        with pytest.raises(ValidationError):
            AIReportSchema.model_validate(data)


class TestSearchPlanSchema:
    def test_valid_search_plan_passes(self):
        plan = SearchPlanSchema.model_validate(
            {"mode": "search", "keywords": ["Harness Engineering", "AI 测试"], "count": 6, "depth": "advanced"}
        )
        assert plan.count == 6
        assert plan.depth == "advanced"

    def test_valid_extract_plan_passes(self):
        plan = SearchPlanSchema.model_validate({"mode": "extract", "url": "https://docs.tavily.com"})
        assert plan.url == "https://docs.tavily.com"
        assert plan.count == 5  # 默认值
        assert plan.depth == "basic"

    def test_rejects_search_without_keywords(self):
        with pytest.raises(ValidationError):
            SearchPlanSchema.model_validate({"mode": "search", "keywords": []})

    def test_rejects_search_with_four_keywords(self):
        with pytest.raises(ValidationError):
            SearchPlanSchema.model_validate({"mode": "search", "keywords": ["a", "b", "c", "d"]})

    def test_rejects_keyword_too_long(self):
        with pytest.raises(ValidationError):
            SearchPlanSchema.model_validate({"mode": "search", "keywords": ["超" * 31]})

    def test_rejects_extract_without_url(self):
        with pytest.raises(ValidationError):
            SearchPlanSchema.model_validate({"mode": "extract", "url": ""})

    def test_rejects_search_with_url(self):
        with pytest.raises(ValidationError):
            SearchPlanSchema.model_validate(
                {"mode": "search", "keywords": ["词"], "url": "https://example.com"}
            )

    def test_rejects_extract_with_keywords(self):
        with pytest.raises(ValidationError):
            SearchPlanSchema.model_validate(
                {"mode": "extract", "url": "https://example.com", "keywords": ["词"]}
            )

    def test_rejects_count_out_of_range(self):
        with pytest.raises(ValidationError):
            SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"], "count": 9})


class TestReportSchema:
    def test_report_schema_composition(self):
        """ReportSchema = 代码计算的统计字段 + AI 生成字段（服务层组装）"""
        report = ReportSchema.model_validate(
            {
                "correct_rate": 80,
                "correct_count": 4,
                "total_questions": 5,
                **make_valid_ai_report(),
            }
        )
        assert report.correct_rate == 80
        assert report.quote == "光既是波，也是粒子"

    def test_rejects_rate_out_of_range(self):
        data = {
            "correct_rate": 101,
            "correct_count": 4,
            "total_questions": 5,
            **make_valid_ai_report(),
        }
        with pytest.raises(ValidationError):
            ReportSchema.model_validate(data)
