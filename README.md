# note-plugins

一个本地 Codex 插件集合，把老师的课件（PPT / PPTX / PDF / 图片）转换成可编辑的 Markdown 笔记，并附带公式规范化、OCR、进度保存和大文件分片等能力。

插件本体就是这个仓库：`.codex-plugin/plugin.json` 是插件清单，`skills/` 是技能，`hooks/` 是钩子。

---

## 目录

- [1. 能力概览](#1-能力概览)
- [2. 环境要求](#2-环境要求)
- [3. 安装](#3-安装)
- [4. 快速开始（转换文件）](#4-快速开始转换文件)
- [5. 输出结构](#5-输出结构)
- [6. 故障排查](#6-故障排查)
- [7. 仓库结构](#7-仓库结构)
- [8. 维护与更新](#8-维护与更新)

---

## 1. 能力概览

本插件包含以下技能（skill）：

| 技能 | 作用 | 典型触发词 |
| --- | --- | --- |
| `courseware-to-notes` | 顶层路由：判断课件类型，分发到对应技能，并强制统一运行环境 | 把课件转成笔记、整理老师课件 |
| `ppt-to-md-for-obsidian` | PPT/PPTX/PDF 课件 → Obsidian 风格 Markdown 课程笔记 | PPT转笔记、课件转Markdown |
| `note-postprocess` | 成稿后清理：重复大标题去重、链接修复、导航整理、全库校验 | 笔记去重、重复大标题、整理笔记库 |
| `pdf-ocr-to-markdown` | PDF/图片 → Markdown（含 OCR、表格、图、公式结构） | OCR、识别PDF、图片转文字 |
| `math-physics-complete` | 公式/物理/数学内容的严谨化（符号、单位、边界条件） | 公式推导、物理建模、量纲分析 |
| `pause-resume-checkpoint` | 进度断点保存与恢复（`暂停/继续`） | 暂停、保存进度、继续、恢复 |
| `payload-safe-batch` | 大文件拆成小任务、每个新会话执行、逐任务更新进度，专治 413 | Payload Too Large、请求体过大、大文件分批 |

---

## 2. 环境要求

- **Codex CLI**：用于安装插件和运行技能。钩子配置需要较新版本（本仓库按 Codex 0.160.1 的 `[hooks]` schema 编写）。
- **conda + `NoteWorkplace` 环境**：本集合的所有 Python 脚本都必须跑在 `NoteWorkplace` 里，**不要**用 `base` 或系统 `python3`。
- **LibreOffice**（可选）：`soffice` 用于把旧版 `.ppt` 先转成 `.pptx`。仅处理 `.pptx` / `.pdf` / 图片时不需要。
- **支持图片输入的模型**（可选）：PDF/图片的视觉 OCR 需要一个能看图的模型；没有时只能完成文本层提取，视觉页会标记为待处理。

下面用占位符表示路径，请替换成你自己机器上的实际路径（把 `<你的用户名>` 换成你的登录名）：

```bash
export NOTE_ENV=/home/<你的用户名>/Programs/miniconda3/envs/NoteWorkplace
export NOTE_PY="$NOTE_ENV/bin/python"
export PLUGIN_REPO=/home/<你的用户名>/plugins/note-plugins
```

---

## 3. 安装

### 3.1 安装 Codex 插件

插件通过「marketplace（插件市场）」安装。一个本地 marketplace 至少要有一个清单文件：

```text
<marketplace-root>/.agents/plugins/marketplace.json
```

内容示例（把本地仓库目录挂成插件）：

```json
{
  "name": "personal",
  "interface": { "displayName": "Personal" },
  "plugins": [
    {
      "name": "note-plugins",
      "source": { "source": "local", "path": "./plugins/note-plugins" },
      "policy": { "installation": "AVAILABLE", "authentication": "ON_INSTALL" },
      "category": "Productivity"
    }
  ]
}
```

其中 `path` 相对 marketplace 根目录解析。添加市场并安装插件：

```bash
# 1) 添加本地 marketplace（指向包含 .agents/plugins/marketplace.json 的目录）
codex plugin marketplace add /home/<你的用户名>

# 也可从 Git 添加：codex plugin marketplace add owner/repo --ref main

# 2) 确认 marketplace 已加载
codex plugin marketplace list

# 3) 安装插件（PLUGIN@MARKETPLACE）
codex plugin add note-plugins@personal

# 4) 确认状态应为 installed, enabled
codex plugin list
```

安装后会在 `~/.codex/plugins/cache/<marketplace>/note-plugins/<version>/` 生成缓存副本，并在 `~/.codex/config.toml` 写入：

```toml
[plugins."note-plugins@personal"]
enabled = true
```

卸载：`codex plugin remove note-plugins@personal`。

### 3.2 信任钩子（hooks）

插件自带钩子（进度保存、会话结束记录、清理），见 `hooks/hooks.json`：

- `Interrupt` / `PreCompact` → 保存进度检查点
- `SessionEnd` → 记录会话结束
- `PostToolUse` → 清理已完成的 OCR 运行目录

**首次运行会提示是否信任这些命令**，需要同意后钩子才会执行。信任状态记录在 `~/.codex/config.toml` 的 `[hooks.state]` 里（`trusted_hash`）。如果发现进度没有保存，先在 Codex 里检查钩子是否已启用/信任。

> 钩子命令使用 `${PLUGIN_ROOT}` 解析到插件根目录，所以无论插件装在哪里都能找到脚本。

### 3.3 创建并校验 `NoteWorkplace` 环境

如果环境还不存在：

```bash
conda create -n NoteWorkplace python=3.11 -y
```

安装本集合需要的依赖（装进 `NoteWorkplace`，不要装到 `base`）：

```bash
conda run -n NoteWorkplace python -m pip install \
  "python-pptx>=1.0.2" "PyYAML>=6.0" "pypdf>=5.0.0" pdfplumber PyMuPDF Pillow
```

校验解释器确实指向该环境（这是本集合的硬性运行时规则）：

```bash
conda run --no-capture-output -n NoteWorkplace python -c \
  'import sys; assert sys.executable.startswith("/home/<你的用户名>/Programs/miniconda3/envs/NoteWorkplace/")'
```

也可以用 OCR 技能自带的依赖脚本检查/修复：

```bash
"$NOTE_PY" "$PLUGIN_REPO/skills/pdf-ocr-to-markdown/scripts/setup_dependencies.py" --check
# 需要修复时（会往当前解释器所在环境安装缺失包）：
"$NOTE_PY" "$PLUGIN_REPO/skills/pdf-ocr-to-markdown/scripts/setup_dependencies.py" --install
```

### 3.4 配置工作区（可选，但推荐）

在你要产出笔记的工作目录放一个 `note-plugins.toml`，统一指定「工作区」——所有运行产物都写到它下面：

```toml
[note_plugins]
# <work_dir>/.codex-progress   进度检查点 + 会话结束日志
# <work_dir>/.ocr_work         OCR 运行产物与视觉原始输出
work_dir = "."
```

- 相对路径相对该文件所在目录解析。
- 找不到配置时退回到会话当前目录。
- 当项目目录只读（例如插件仓库本身被挂成只读）时，把它指向一个可写目录。
- 兼容旧配置 `~/.config/note-plugins/progress.toml` 的 `[progress].base_dir`。

---

## 4. 快速开始（转换文件）

所有脚本用 `NoteWorkplace` 的解释器运行。下文用 `$NOTE_PY` 代替完整路径，先执行第 2 节的环境变量导出。

### 4.1 PPT / PPTX / PDF 课件 → 笔记

最省事的方式：在 Codex 里把会话切到课程目录，直接给文件并说「使用note-plugins插件把课件转成笔记」。顶层技能会路由到 `ppt-to-md-for-obsidian`。

也可以手动跑确定性提取管线（生成原始提取产物 + 清单，再由 Codex/AI 或人改写成笔记）：

```bash
# 整条管线：提取 + 清理 + 写 pipeline_manifest.md
"$NOTE_PY" "$PLUGIN_REPO/skills/ppt-to-md-for-obsidian/scripts/ppt_to_obsidian_pipeline.py" \
  "/path/to/课件.pptx" --output-dir "notes/课程名/raw"

# 旧版 .ppt 需要 LibreOffice 先转换（自动尝试），或手动指定：
"$NOTE_PY" "$PLUGIN_REPO/skills/ppt-to-md-for-obsidian/scripts/ppt_to_obsidian_pipeline.py" \
  "/path/to/课件.ppt" --soffice /usr/bin/soffice --output-dir "notes/课程名/raw"
```

只想快速做一次文字提取：

```bash
"$NOTE_PY" "$PLUGIN_REPO/skills/ppt-to-md-for-obsidian/scripts/extract_pptx_text.py" "<file.pptx>"
```

常用参数：`--mode {course-notes,research-presentation,exam-review}`、`--unicode-math`、`--config <yaml>`。

### 4.2 PDF / 图片 → Markdown（OCR）

在目标工作目录下运行（默认把内部产物写到 `.ocr_work/`，可编辑的 Markdown sidecar 写在源文件旁边）：

```bash
"$NOTE_PY" "$PLUGIN_REPO/skills/pdf-ocr-to-markdown/scripts/batch_ocr.py" \
  "/path/to/a.pdf" "/path/to/b.png" --keep-work
```

流程：依赖检查 → 逐页提取文本 → 渲染页面图 → 逐页质量分类与路由 → 生成每页提示 → 合并产物 → 生成 `<safe_stem>_ocr.md`。

接下来（在 Codex 里）会读 `.ocr_work/ocr_output/run_*/batch_summary.json` 和每页的 `route_report.json`：

- 全部页是 `text_only` → 直接给你源文件旁的 Markdown，状态 `complete`。
- 存在视觉页（`visual_focus` / `verify_text_and_visual` / `full_vision_ocr`）→ Codex 逐页用图片查看能力检查渲染图，写原始页 JSON，再合并：

  ```bash
  "$NOTE_PY" "$PLUGIN_REPO/skills/pdf-ocr-to-markdown/scripts/merge_outputs.py" \
    --run-out-dir .ocr_work/ocr_output/run_YYYYMMDD_HHMMSS \
    --work-run-dir .ocr_work/run_YYYYMMDD_HHMMSS
  ```

  视觉页只看**缩略图预览** `page_NNNN.vision.jpg`（渲染时自动生成，最长边默认 1568px、JPEG q80），**不要**把全分辨率 `page_NNNN.png` 交给模型——240 DPI 单页 PNG 常有 1–5MB，base64 后必然触发 413。可用 `--vision-max-edge` / `--vision-quality` 调整；对已渲染的旧运行可用下面这个脚本补生成预览，无需重新渲染：

  ```bash
  "$NOTE_PY" "$PLUGIN_REPO/skills/pdf-ocr-to-markdown/scripts/make_vision_previews.py" \
    .ocr_work/run_YYYYMMDD_HHMMSS/rendered_pages --recursive
  ```

  图（`[Figure: ...]`）会变成真正的图片：视觉页给 `figure` 块带一个归一化 `bbox`，合并时把该区域从渲染页裁出来，写到 sidecar 旁边的 `<safe_stem>_ocr_assets/` 并在笔记里嵌入 `![label](./<safe_stem>_ocr_assets/fig_p0001_01.png)`。用 `--figures {crop,page,placeholder}` 控制（默认 `crop`；无 bbox 时先按墨迹边界裁掉页边，再由 `--figure-max-edge`（默认 1600）封顶尺寸，兜底页存 JPEG）。若已有原始页 JSON，可让首次 `batch_ocr` 直接出图：`--raw-from <dir>`（指定某次 run 的工作目录，或其 `raw_agent_outputs/`）或 `--reuse-raw`（自动取 `--work-dir` 下最近一次）。移动笔记时 `.md` 和 `_assets/` 一起移动即可。

- 当前运行时无法看图片 → 状态为 `text_extraction_complete_visual_pending`（**不是** `complete`），会如实告知视觉页待处理。

确认 Markdown 无误后，可清理隐藏产物（会保留 `<safe_stem>_ocr.md`）：

```bash
"$NOTE_PY" "$PLUGIN_REPO/skills/pdf-ocr-to-markdown/scripts/cleanup_workdir.py" \
  .ocr_work/run_YYYYMMDD_HHMMSS \
  --out-run-dir .ocr_work/ocr_output/run_YYYYMMDD_HHMMSS --delete-out-run
```

### 4.3 大文件 / 报 `Payload Too Large`

`Payload Too Large: Failed to buffer the request body` 是 API 网关返回的 413：单次请求体（历史对话 + 最新工具结果）超过了网关缓冲上限。注意：

- **钩子抓不到这个错误**（没有「请求失败」类钩子事件），所以只能靠「预防 + 持续存盘」。
- 真正有效的做法是把大文件拆成小任务，每个任务用**新会话**执行，避免把全部内容塞进一次请求。

用 `payload-safe-batch`：

```bash
# 1) 盘点目标文件并生成分片计划（只读元信息，不读正文）
"$NOTE_PY" "$PLUGIN_REPO/skills/payload-safe-batch/scripts/plan_payload_split.py" \
  "/path/to/大文件.pdf" --budget-bytes 160000

# 2) 先预览每个分片的命令（不执行）
"$NOTE_PY" "$PLUGIN_REPO/skills/payload-safe-batch/scripts/run_payload_split.py" \
  --plan .payload_work/payload_plan.json

# 3) 真正执行：每个分片一个新会话，逐分片更新进度
"$NOTE_PY" "$PLUGIN_REPO/skills/payload-safe-batch/scripts/run_payload_split.py" \
  --plan .payload_work/payload_plan.json --execute
```

特性：PDF 按页切，图片一图一单元，文本按行区间切；某分片真的中 413 时自动对半拆成 `_a/_b` 重试（`--max-depth` 默认 3）；进度写入 `.payload_work/payload_progress.json` 与 `<cwd>/.codex-progress/checkpoint.md`；重跑会跳过已完成分片。默认命令模板是 `codex exec ...`（全新会话），可用 `--cmd-template` 覆盖。

### 4.4 暂停 / 恢复

在 Codex 会话里说 `暂停` / `保存进度` / `stop`，或按 `Esc` 中断：`Interrupt` 钩子会把检查点写到 `<work_dir>/.codex-progress/checkpoint.md`（含目标、阶段、已完成、下一步、阻塞项）。说 `继续` / `恢复` / `resume` 时读取该文件并从下一步接着做。上下文变大触发压缩前，`PreCompact` 钩子也会先存一次进度，作为 413 的早期预警。

### 4.5 成稿后清理（重复大标题去重）

笔记写完后再做，不参与课件抽取。重复大标题指的是「笔记标题和紧随其后的同名小节标题」或「同一小节标题连续出现两次」。先看报告，再就地修复：

```bash
# 只报告；有文件需要改动时退出码为 1
"$NOTE_PY" "$PLUGIN_REPO/skills/note-postprocess/scripts/dedupe_headings.py" \
  "notes/课程名" --check

# 就地修复（默认行为）
"$NOTE_PY" "$PLUGIN_REPO/skills/note-postprocess/scripts/dedupe_headings.py" \
  "notes/课程名"
```

只有三条同时满足才删：文字归一化后相同（忽略全/半角、大小写、首尾空白、结尾 `#`、`{#id}` 和结尾冒号）、两个标题之间除了空行没有任何正文、后一个标题层级不浅于前一个。中间有内容重名的标题会被保留；frontmatter 和代码块内的 `#` 不当作标题。修复后建议重新跑一次链接与结构检查（见 `skills/ppt-to-md-for-obsidian/scripts/`）。

---

## 5. 输出结构

面向用户的笔记（可继续手动编辑）：

```text
notes/
  XXX课程/
    00_课程总览.md
    01_章节名.md
    知识点详细版_含公式.md
    知识点精简复习版_含公式.md
    XXX课件.pdf                   # 源文件（只读，保持不动）
    XXX课件_ocr.md                # PDF/图片 OCR 的 sidecar
```

内部产物（默认隐藏，通常最后清理）：

```text
.codex-progress/                 # 进度检查点、会话结束日志
.ocr_work/
  ocr_output/run_YYYYMMDD_HHMMSS/
    batch_summary.json/.md
    files/<source_id>/final.md, final.json, pages/...
  run_YYYYMMDD_HHMMSS/           # 渲染页图、每页提示、原始视觉输出
.payload_work/                   # 大文件分片计划与分片输出
```

---

## 6. 故障排查

**出现 `Payload Too Large: Failed to buffer the request body`**
单次请求体超限，最常见的是**图片**和**大文件读取**两类：

- 看渲染页图时报错 → 说明发的是全分辨率 PNG。改用 `page_NNNN.vision.jpg` 缩略图；旧运行用 `make_vision_previews.py` 补生成（见 4.2）。同时不要在一个会话里累积查看很多张图，长跑用 `payload-safe-batch` 每页一个新会话。
- 读大文件/合并大 Markdown 时报错 → 用 `payload-safe-batch` 分片（见 4.3），或降低 `--budget-bytes` 重新规划。

已完成的进度在 `.codex-progress/checkpoint.md` 里，可从断点续跑。

**进度没有保存**
1) 确认插件 hooks 已被信任（`~/.codex/config.toml` 的 `[hooks.state]`）；2) 确认 `<work_dir>` 可写（只读项目请在 `note-plugins.toml` 指向可写目录）；3) 检查 `.codex-progress/` 是否生成。

**脚本报缺依赖 / `ModuleNotFoundError`**
确认用的是 `NoteWorkplace` 解释器，然后 `conda run -n NoteWorkplace python -m pip install ...` 或跑 `setup_dependencies.py --install`。不要用系统 `python3` 或 `base`。

**PDF/图片的视觉页一直是 pending**
当前运行时没有可用的图片检查能力，或模型不支持图片输入。只会完成文本层提取，状态为 `text_extraction_complete_visual_pending`，不会谎报完成。换用支持图片输入的模型后重跑视觉页并合并。

**旧版 `.ppt` 转换失败**
确认装了 LibreOffice（`soffice`），或在命令里 `--soffice /usr/bin/soffice`。管线会回退到内置的 OLE/CFB 文本记录提取，并把该文件标记为部分/OCR受限，而不是谎称完整覆盖。

**换了机器 / 路径变了**
把第 2 节的环境变量和第 3.4 节的 `work_dir` 改成新路径；`hooks.json` 用 `${PLUGIN_ROOT}` 自动定位，无需改脚本路径。

---

## 7. 仓库结构

```text
note-plugins/
  .codex-plugin/plugin.json        # 插件清单（name/version/skills/hooks）
  hooks/hooks.json                 # 钩子声明（Interrupt/PreCompact/SessionEnd/PostToolUse）
  .codex/config.toml               # 项目级钩子模板（用 ${PLUGIN_ROOT}，无本机绝对路径）
  skills/
    courseware-to-notes/           # 顶层路由 + 运行时规则
    ppt-to-md-for-obsidian/        # PPT/PPTX/PDF -> Obsidian 笔记（含 scripts/ 与 tests/）
    note-postprocess/              # 成稿后清理：重复标题去重 + 校验（含 scripts/ 与 tests/）
    pdf-ocr-to-markdown/           # PDF/图片 OCR（提取/渲染/分类/合并/清理/视觉预览）
    math-physics-complete/         # 数学/物理公式严谨化
    pause-resume-checkpoint/       # 进度断点（脚本 + hooks 配置示例）
    payload-safe-batch/            # 大文件分片 + 进度账本（plan/run 两个脚本）
```

---

## 8. 维护与更新

- 修改技能时，改**源仓库**（本目录），不要直接改 `~/.codex/plugins/cache/...` 里的缓存副本。
- 若插件市场来自本地路径（`source: local`），重新 `codex plugin add note-plugins@personal` 即可刷新缓存；来自 Git 的用 `codex plugin marketplace upgrade`。
- `ppt-to-md-for-obsidian` 自带校验：`"$NOTE_PY" skills/ppt-to-md-for-obsidian/scripts/validate_skill_repo.py`（以脚本 `--help` 为准）。
- 新增/修改技能后，确认 `skills/<name>/SKILL.md` 的 frontmatter 有 `name` 与 `description`，插件才会把它注册进技能列表。

---

## 9. 许可

- 本仓库自身（含 `courseware-to-notes`、`note-postprocess`、`pause-resume-checkpoint`、`payload-safe-batch`、`math-physics-complete`）按根目录 [LICENSE](LICENSE) 授权：**MIT**。
- 打包进来的第三方技能保留其原始许可与署名，详见 [NOTICE.md](NOTICE.md)：
  - `ppt-to-md-for-obsidian` — MIT，© 2026 blacksheep1118
  - `pdf-ocr-to-markdown` — MIT，© 2026 jaeseok11
