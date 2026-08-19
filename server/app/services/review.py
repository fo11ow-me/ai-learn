"""错题重练服务（WHY：遗忘曲线调度与错题聚合集中一处；纯函数便于单测穷举状态转移）

SM-2 简化规则（方案设计文档-用户系统 §5.x 契约）：错题收录即错过 1 次，初始间隔 1 天；
每次重练答对，下次间隔按序列递增（2/4/7 天），连续答对 3 次标记已掌握；重练答错则
重置回 1 天间隔且累计错过次数 +1。
"""
from datetime import date, datetime, timedelta

REVIEW_INTERVAL_DAYS = [1, 2, 4, 7]  # 初始/递增间隔（天）；REVIEW_INTERVAL_DAYS[0] 同时是答错重置间隔
MASTERED_STREAK = 3  # 连续答对 3 次即掌握
STATUS_PENDING = "pending"
STATUS_MASTERED = "mastered"
