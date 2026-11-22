# 本地学习记录

所有命令均使用绝对路径指定同一个课程目录。`learning-records.sqlite3` 存在该目录内。文件输入使用 UTF-8 JSON，命令返回 JSON，失败时返回非零退出码。从技能根目录执行以下命令，将尖括号内容替换为对应路径。首次初始化须确认保存位置，已有记录库搬迁后在新目录执行 `confirm-storage`，保留已有记录。普通记录写入、资料转换和索引要求先确认当前解析后的绝对路径。模型准备不依赖课程目录确认。仅读操作可访问已有旧库。

```text
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" init --file profile.json --confirm-storage
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" profile
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" update-profile --file profile-update.json
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" save-exam --file exam.json
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" exams --limit 20 --topic 轴向应力
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" exam --exam-id exam-001
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" exam --exam-id exam-001 --key
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" submit --file attempt.json
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" grade --file grade.json
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" attempt --attempt-id attempt-001
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" status
```

旧库或搬迁后应先执行 `python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" confirm-storage`。该命令要求当前目录含有效课程档案，不创建新课程。

`init` 示例为 `{"course":"材料力学","scope":"第 1–5 章"}`，可额外保存考试日期、题型、时长、满分、教材、目标等。`update-profile` 将输入对象的顶层字段合并到现有档案，嵌套字段整体替换。缺失的信息标记为未知，不推测。仅在用户明确希望某课程的偏好持续使用时，将其写入可选顶层 `exam_preferences` 或 `feedback_preferences`，值可以是文字或对象。更新前先读取并合并要保留的偏好，嵌套对象会整体替换。当前请求优先于保存的偏好，偏好不能改变题目依据或评分正确性。

`exam.json` 样例：

```json
{
  "id": "exam-001", "title": "第一章诊断练习", "mode": "diagnostic",
  "instructions": "请写出必要步骤", "duration_minutes": 30,
  "questions": [{
    "id": "q1", "prompt": "题目正文", "points": 5, "topic": "轴向应力",
    "answer": "参考答案", "rubric": "计算式 3 分，单位 2 分",
    "basis": "direct", "basis_note": "对应资料中轴向应力的定义和公式",
    "sources": [{"source_id": "source-abc.md", "locator": "Page 2", "chunk_id": "chunk-id"}]
  }]
}
```

`answer` 为非空字符串，`rubric` 为非空字符串、列表或对象。新题 `basis` 须为 `direct` 或 `variant`，`basis_note` 须为非空说明。每题必须提供真实检索所得来源三元组 `source_id`、`locator` 和 `chunk_id`，保存时 CLI 会核对其在当前新鲜索引中存在。助手须另外核对具体内容、范围和排除项，确保来源实质支持题目。变式须以已核验的例题为依据并保留考查概念、方法、题设假设和难度，除非用户要求调整难度。按 [出卷与复练](exam-workflow.md) 触发公开考研题备选时，核验并简要改述为 Markdown 后入库，在档案 `source_roles` 把该来源 ID 标记为 `public_grad_exam`。只有大纲或主题相近片段时应说明资料缺口，不能据此自行补题。详细准则见 [出卷与复练](exam-workflow.md)。

来源对象可附标题快照及 `source_url`。公开考研题备选必须保存核验过的原始页面 `source_url`，批改时逐题引用该 URL，题目仍须保留真实索引三元组。年份、学科与题号仅在来源已核实时记入。题目可以有 `options`。向学生显示时调用不带 `--key` 的 `exam`，参考答案与评分细则通过 `--key` 查阅。修改试卷时生成新的 `id` 并设置 `parent_exam_id`，旧卷不可覆盖。同一 ID 及内容重试是幂等的，不同内容会报错。已有历史试卷保留原样，仍可提交和批改，不需要补填新增出题字段。

`attempt.json` 使用 `{"id":"attempt-001","exam_id":"exam-001","answers":{"q1":"作答正文"}}`。保存用户原始作答，允许省略未答题或以空字符串表示，未知题号会被拒绝。每次作答使用新 ID，已有内容不会覆盖。

`grade.json` 使用 `{"attempt_id":"attempt-001","results":[{"question_id":"q1","score":3,"feedback":"单位未写"}]}`。必须覆盖试卷每题且仅出现一次。得分须在零和该题分值之间，无法判定时使用 `"score": null`，在反馈说明原因。每次更正批改都会追加记录，完全相同的最近一次批改重试不会重复保存。`attempt` 返回原作答和所有批改版本，`status` 使用每次作答最新版本，对已判题统计主题得分、最近五条及未满分题目。未批改及待评题不计入分母。统计反映练习表现，样本不足时不可宣称掌握程度已确定；复习优先级还应考虑考试权重、时间和题目覆盖。

`exams` 为助手出题前查询的历史视图，覆盖尚未作答的试卷，返回题目摘要和知识点以帮助查重。默认返回最近 20 卷，可用 `--limit` 指定 1–200，`--topic` 按知识点子串筛选。该视图供助手使用，学生考试视图不显示知识点标签，以免提示解法。
