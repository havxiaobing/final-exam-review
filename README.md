# final-exam-review

Final Exam Review is a Codex skill for university exam preparation. It builds a searchable local library from lecture slides, PDFs, homework, and past exams, then creates practice exams based on those materials. After grading, it uses the student's work to identify gaps, explains difficult concepts with diagrams alongside the relevant questions, and tracks progress across study sessions.

这个技能帮助用户按课程考试范围准备练习。它将用户指定的 PDF、PPTX、Markdown 或文本转换成 Markdown，在用户选择的目录建立本地语义索引，优先按作业、样卷和课件内容或例题变式出卷，并支持批改、图解错题、复练和持续记录学习情况。使用时向支持技能的助手提供课程资料位置、课程目录和备考目标，具体工作流见 [SKILL.md](SKILL.md)。

## 安装与准备

在 Codex 中，将此仓库克隆到用户技能目录。以下是 Windows PowerShell 示例，把 `YOUR_USERNAME` 换成仓库所有者：

```powershell
$skillRoot = Join-Path $env:USERPROFILE '.agents/skills'
New-Item -ItemType Directory -Path $skillRoot -Force | Out-Null
git clone https://github.com/YOUR_USERNAME/final-exam-review.git (Join-Path $skillRoot 'final-exam-review')
```

只在一个项目中使用时，将技能目录放入该项目的 `.agents/skills/`。安装依赖后在 Codex 输入 `$final-exam-review`。可以查阅 [Codex 的技能安装说明](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)。

建议使用 Python 3.13，在独立环境中安装依赖：

```text
python -m venv <环境目录>
<环境中的python> -m pip install fastembed==0.9.1 pymupdf python-pptx
```

Windows 环境中的 Python 通常位于 `<环境目录>/Scripts/python.exe`，macOS 和 Linux 通常位于 `<环境目录>/bin/python`。也可以使用 uv 创建环境并安装同样的包。课程目录由用户明确选择。模型缓存可以复用已准备的目录，也可以单独指定。建议将两者放在技能源码仓库之外，命令使用绝对路径。以下示例中的 `python` 代表该环境的 Python，命令从技能根目录运行，尖括号内容替换为实际路径。

```text
python -X utf8 scripts/records.py --course-dir "<课程目录绝对路径>" init --file "<课程档案JSON路径>" --confirm-storage
python -X utf8 scripts/convert.py --course-dir "<课程目录绝对路径>" "<课件文件路径>"
python -X utf8 scripts/library.py --course-dir "<课程目录绝对路径>" prepare-model --model "<模型缓存绝对路径>"
python -X utf8 scripts/library.py --course-dir "<课程目录绝对路径>" index --model "<模型缓存绝对路径>"
python -X utf8 scripts/library.py --course-dir "<课程目录绝对路径>" search --query "<需要检索的知识点>"
```

最小课程档案示例为 `{"course":"材料力学","scope":"第 1–5 章"}`，以 UTF-8 JSON 保存。请根据用户指定的位置填写课程目录。若已有记录库迁移到新路径，执行 `python -X utf8 scripts/records.py --course-dir "<新课程目录绝对路径>" confirm-storage`，再在新路径写入、转换或建库。该命令要求当前位置已有有效课程档案。移动模型缓存后，用新路径重新运行 `index`。

Windows 上，如果之前已准备专用运行环境和模型缓存，可通过 PowerShell 检查后复用，不依赖特定用户名或盘符：

```powershell
$support = Join-Path $env:LOCALAPPDATA 'Codex/final-exam-review'
$python = Join-Path $support 'runtime/Scripts/python.exe'
$model = Join-Path $support 'model-cache'
if (Test-Path -LiteralPath $python) { & $python --version }
if (Test-Path -LiteralPath $model) { Write-Output $model }
```

## 使用方式

可以向助手说：“这是考试范围、课件和老师的作业，请用我指定的课程目录建立资料库，先出一份诊断练习，等我作答后批改并记录错题。” 也可以提供老师的题型、分值、考试时间、往年题和当前薄弱章节，要求生成限时模拟卷或专项练习。提交后默认按得分、错题分析、试卷总结反馈。错题分析逐题展示该次作答对应版本的原题、错误与正确解法、知识点和来源，并在每题下用情景图或知识点图说明这道题的解题关系。原题的选项、小问、必要数据及已保存原题图一并呈现。主要失分题依据作答定位知识缺口并重点补讲，孤立的小错简短订正。你可以明确指定出题风格和回答方式，也可以把偏好存为该课程的默认设置。后续对话继续使用同一课程目录，助手可读取历史题目、作答和掌握情况。

优先采用用户提供的作业、练习、样卷及往年题，其次参考 PPT 例题。若没有可用的 PPT 且没有其他出题资料，助手先询问是否能提供教材或笔记。用户确认没有，或直接要求联网搜索时，可查找对应学科公开考研题，出卷前说明外部来源与本课程的适配尚未校准。题目须匹配已知考试范围，并记录已核验的公开来源。

课件中的扫描页、图片、图表、表格和公式可能需要人工核对或补充，具体见 [资料转换说明](references/conversion.md)。命令和来源定位见 [向量检索说明](references/vector-library.md)，档案、试卷和作答格式见 [学习记录说明](references/records.md)。

## 数据与联网

转换、向量索引和学习记录写入用户指定的本地目录。安装依赖及首次准备模型会联网下载软件或模型，向量索引和查询使用本地缓存。满足上述公开考研题备选条件时，助手还会联网检索并核验公开来源。助手读取检索结果后，数据如何由助手服务处理取决于实际使用的平台和配置。课程文件、作答和个人复习记录由用户自行决定保存和分享的位置。

## 验证

在技能根目录运行自动测试：

```text
python -X utf8 -m unittest discover -s tests -v
```

自动回归位于 `tests/`。对话用例见 [evals.json](evals/evals.json)，人工验收和发布前复现步骤见 [测试说明](references/testing.md)。真实课程资料、学习记录、模型缓存和虚拟环境请保存在仓库之外。

## 第三方组件

PDF 转换使用 PyMuPDF，其发布方提供 AGPL 和商业许可。模型权重由首次准备步骤单独下载。参见 [PyMuPDF 许可说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)。
