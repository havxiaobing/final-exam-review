# 本地向量检索

输入为用户指定课程目录的 `materials/*.md`。每个 Markdown 对应一个原文件，二级标题 `## Page N`、`## Slide N` 或 `## Section N` 记录来源位置。索引为课程目录下的 `course-index.sqlite3`，模型缓存路径由用户选择。

使用已安装 `fastembed==0.9.1` 的 Python 环境。从技能根目录运行，以下尖括号内容应替换为对应绝对路径：

```text
python -X utf8 scripts/library.py --course-dir "<课程目录绝对路径>" prepare-model --model "<模型缓存绝对路径>"
python -X utf8 scripts/library.py --course-dir "<课程目录绝对路径>" index --model "<模型缓存绝对路径>"
python -X utf8 scripts/library.py --course-dir "<课程目录绝对路径>" status
python -X utf8 scripts/library.py --course-dir "<课程目录绝对路径>" search --query "梁的弯曲正应力公式和适用条件" --limit 6
```

`prepare-model` 从 Hugging Face 下载 Qdrant 提供的 BAAI/bge-small-zh-v1.5 的 ONNX 模型，约 90 MB。下载过程不读取课程内容。安装依赖和首次准备模型需要联网，之后的索引和检索使用 `local_files_only=True`，不向模型下载服务上传材料或问题。已有完整 FastEmbed 模型缓存时可以直接使用该缓存。建议将模型缓存单独存放，避免不相关文件变化导致索引需要重建。助手或使用的平台在处理检索结果时可能有其自身的数据处理方式。

首次建库前，先按 [本地学习记录](records.md) 初始化并确认课程目录。已有记录库搬迁后，须在新目录执行 `confirm-storage`。`index` 完整重建并在完成后替换索引，失败时保留现有索引。`status` 检查材料内容和模型缓存变化。`search` 在模型或任何 Markdown 改变时要求先重建，返回片段 ID、`source_id`（`materials` 中包含 `.md` 后缀的精确文件名）、原文件名、原页/幻灯片/章节定位、相似度和不超过约 350 字的片段。

`--source` 可重复指定 Markdown 精确文件名，用来限定范围，例如 `--source chapter1-a1b2.md`。没有结果或转换遗漏图片内容时应说明。语义相似度用于排序，重要考点应换不同表述搜索并检查转换质量。题目应逐题保留引用定位，证据不足时说明不确定性。

索引记录模型缓存的绝对路径。移动缓存后须用新路径重新运行 `index --model`。模型缓存整体搬迁且文件完整时仍可离线加载。日志和错误信息可能来自 FastEmbed，脚本结果是 JSON。
