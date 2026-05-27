# md2json-api

把数学教材、讲义、论文 Markdown 一键转换成参考 workflow 使用的 section JSON。

核心设计现在更接近 `ref` 中的思路：

- 脚本负责稳定、可重复的工程工作：文件组织、trace、schema 校验、label 归一、质量报告。
- LLM 负责主要语义工作：规划 chapter/section 结构、判断 definition/theorem/proof/example/algorithm 等数学条目、修复 proof 边界。
- 本地/mock backend 只用于 smoke test，不代表最终抽取质量。

- 总文件：`<input_stem>.json`
- 修复后的分 section 文件：`sections/section01.json ...`
- LLM 初抽结果：`initial_sections/section01.json ...`
- 原始 section 切片：`source_md_sections/section01.md ...`
- LLM 审计报告：`audit_reports/section01_audit.md ...`
- LLM patch candidate：`patch_candidates/section01_patch_candidate.json ...`
- 整体审计/patch 汇总：`audit_report_all.md` / `patch_candidate_all.json`
- 结构索引：`structure.json`
- LLM 结构规划：`structure_plan.json`
- 结构候选输入：`structure_candidates.json`
- 质量报告：`quality_report.json` / `quality_report.md`
- 汇总：`summary.json`
- 索引：`INDEX.md`

每个 JSON item 使用参考 skill 的字段：

```json
{
  "index": 1,
  "label": "Theorem 13-13.A-1",
  "env": "thm",
  "number_components": ["13", "1"],
  "context": {
    "chapter": "Chapter 13. First order differentiation",
    "section": "13.A. Ultratangent space",
    "chapter_number": "13",
    "section_number": "13.A"
  },
  "content": "...",
  "dependencies": [],
  "proof": "..."
}
```

## 安装

```bash
cd /home/xuanzhi_ren/Pdf2jsonPipeline/md2json_api_project
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
```

OpenAI API key 推荐只放在环境变量里：

```bash
export OPENAI_API_KEY="你的 key"
```

## 一键转换

```bash
python3 -m md2json_api.cli convert \
  /path/to/book_ch13_pages187-207.md \
  --out-dir /path/to/book_ch13_pages187-207_json \
  --model gpt-5.2
```

也可以安装成命令：

```bash
python3 -m pip install -e .
md2json-api convert /path/to/input.md --out-dir /path/to/output_json
```

完整 Azure 一键启动模板：

```bash
cd /home/xuanzhi_ren/Pdf2jsonPipeline/md2json_api_project

set -a
source ~/.config/md2json/azure.env
set +a

python3 -m md2json_api.cli convert /home/xuanzhi_ren/Pdf2jsonPipeline/Test/Convex-analysis-Ralph-Tyrell-Rockafellar-20260521165850/Convex-analysis-Ralph-Tyrell-Rockafellar.md \
  --backend azure \
  --model "$MD2JSON_MODEL" \
  --azure-endpoint "$AZURE_OPENAI_ENDPOINT" \
  --azure-api-version "$AZURE_OPENAI_API_VERSION" \
  --prompt-profile auto \
  --structure-mode llm \
  --audit-mode llm \
  --out-dir /home/xuanzhi_ren/Pdf2jsonPipeline/Test/Convex-analysis-Ralph-Tyrell-Rockafellar-20260521165850/output_json3
```

如果一次长文档转换中途断开，使用同一个 `--out-dir` 并加 `--resume`：

```bash
python3 -m md2json_api.cli convert /path/to/input.md \
  --backend azure \
  --audit-mode auto \
  --resume \
  --out-dir /path/to/output_json
```

## 先检查 section 切分

```bash
python3 -m md2json_api.cli inspect /path/to/input.md
```

`inspect` 会显示：

- 检测到的 section 顺序和行号；
- 是否创建了 synthetic pre-section；
- 是否排除了 References / Acknowledgements 等 back matter；
- splitter warnings。

## 离线冒烟测试

没有 API key 时，可以用本地规则 backend 跑通输入输出链路：

```bash
python3 -m md2json_api.cli convert examples/chapter13_tiny.md \
  --backend local \
  --out-dir outputs/tiny_local_json
```

本地 backend 只适合干净的编号教材片段和 CI smoke test；正式转换请使用默认 `--backend openai` 或 `--backend azure`。

也可以用 mock API backend 跑完整的“API 边界”链路：

```bash
python3 -m md2json_api.cli convert examples/chapter13_tiny.md \
  --backend mock \
  --prompt-profile auto \
  --structure-mode llm \
  --out-dir outputs/tiny_mock_json
```

`mock` backend 不联网，但会写出结构规划和每个 section 的模拟 API trace：

```text
mock_structure_api_call/
  request.json
  response.json
  call.json
mock_api_calls/
  section01_request.json
  section01_response.json
```

这样可以检查真实 API 会收到的 prompt/schema，以及符合 schema 的模拟响应。新版 mock 不再为没有识别到条目的 section 伪造整节 `remark`，因此如果规则找不到条目会返回空数组，并由 `quality_report` 标出风险。

## Azure OpenAI

如果使用 Azure OpenAI Chat Completions：

```bash
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com"
export AZURE_OPENAI_API_KEY="你的 key"
export AZURE_OPENAI_API_VERSION="2024-10-21"

python3 -m md2json_api.cli convert /path/to/input.md \
  --backend azure \
  --model gpt-5.5 \
  --out-dir /path/to/output_json
```

这里的 `--model` 是 Azure deployment 名称；如果你的部署名不是 `gpt-5.5`，改成实际 deployment。

真实 API backend 会在输出目录记录：

```text
api_calls/
  section01_request.json
  section01_response.json
audit_api_calls/
  section01_request.json
  section01_response.json
structure_api_call/
  request.json
  response.json
  call.json
```

这些 trace 不包含 API key，可用于定位 prompt、schema、模型响应和后处理问题。

注意：trace 虽然不包含 API key，但包含上传文档内容、prompt 和模型输出；`source_md_sections/`
也包含原文。部署为服务时，这些文件必须留在服务端私有作业目录中，不可作为静态文件或下载接口公开。

## HTTP API 服务部署

项目提供异步 HTTP 服务入口。上传会立即返回 `job_id`，转换在后台 worker 中完成，客户端轮询状态后读取最终 JSON。
API 不接收 API key、endpoint、服务器路径或 provider 配置；这些配置只能由服务器管理员提供。

### 服务端配置

生产环境推荐由 `systemd` 的受限 `EnvironmentFile` 注入配置，文件权限设置为仅服务账户可读：

```text
MD2JSON_API_TOKEN=replace_with_a_long_random_service_token
MD2JSON_SERVER_BACKEND=azure
MD2JSON_MODEL=your_azure_deployment
MD2JSON_JOBS_ROOT=/srv/md2json/jobs
MD2JSON_WORKERS=1
MD2JSON_MAX_UPLOAD_BYTES=10485760
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_API_KEY=your_api_key_here
```

使用 OpenAI backend 时，配置 `MD2JSON_SERVER_BACKEND=openai` 和 `OPENAI_API_KEY`；不要在客户端请求、
命令行参数或日志中发送 key。`MD2JSON_WORKERS=1` 是当前文件型输出和恢复机制下最稳妥的部署设置。

启动服务：

```bash
python3 -m pip install -r requirements.txt
python3 -m md2json_api.cli serve --host 127.0.0.1 --port 8000
```

服务默认要求 `MD2JSON_API_TOKEN`。只有隔离的本地测试环境才可显式设置
`MD2JSON_ALLOW_UNAUTHENTICATED=true`。生产环境应由 Nginx 或 Caddy 在前端终止 HTTPS，只代理必要的
API 路径，不要将 `/srv/md2json/jobs` 映射为静态目录。

### API 合约

创建转换任务：

```bash
curl -X POST https://api.example.com/v1/conversions \
  -H "Authorization: Bearer ${MD2JSON_CLIENT_TOKEN}" \
  -F "file=@/path/to/input.md;type=text/markdown" \
  -F "prompt_profile=auto" \
  -F "structure_mode=auto" \
  -F "audit_mode=auto"
```

可由客户端选择的参数仅为：

- `prompt_profile`：`auto`、`textbook`、`paper`、`chinese_math`
- `structure_mode`：`auto`、`llm`、`hard`
- `audit_mode`：`auto`、`llm`、`off`

接口列表：

| Method | Path | 返回内容 |
|---|---|---|
| `GET` | `/healthz` | 存活状态，不包含配置或凭据 |
| `POST` | `/v1/conversions` | `job_id` 和初始任务状态 |
| `GET` | `/v1/conversions/{job_id}` | 状态、阶段和 section 进度 |
| `POST` | `/v1/conversions/{job_id}/resume` | 将失败任务按安全缓存规则重新排队 |
| `GET` | `/v1/conversions/{job_id}/result` | 成功任务的最终 item JSON |
| `GET` | `/v1/conversions/{job_id}/quality` | 脱敏后的质量报告 |

除 `/healthz` 外的接口都需要 bearer token。API 不提供 trace、源 Markdown 切片、内部错误栈或服务器路径。

### `systemd` 示例

服务配置文件 `/etc/md2json/md2json.env` 应由管理员创建并设置为仅服务账户可读；其中 key/token 使用实际本地值，
不要提交到仓库。

```ini
[Unit]
Description=md2json API service
After=network-online.target

[Service]
Type=simple
User=md2json
Group=md2json
WorkingDirectory=/opt/md2json-api-project
EnvironmentFile=/etc/md2json/md2json.env
ExecStart=/opt/md2json-api-project/.venv/bin/python -m md2json_api.cli serve --host 127.0.0.1 --port 8000
Restart=on-failure
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

`MD2JSON_JOBS_ROOT` 下包含上传原文、trace、内部 SQLite 状态库和输出文件，权限应限制在服务账户内，并配置保留期清理。

## 使用 tmux 观察人工任务

`systemd` 适用于 API 服务本身；`tmux` 适用于管理员人工运行一次性长任务或排查转换行为。

创建会话并运行任务：

```bash
tmux new -s md2json
cd /path/to/md2json-api-project
. .venv/bin/activate
python3 -m md2json_api.cli convert /data/input/book.md \
  --backend azure \
  --model "$MD2JSON_MODEL" \
  --prompt-profile auto \
  --structure-mode llm \
  --audit-mode llm \
  --out-dir /data/jobs/manual-001/output
```

常用操作：

```bash
# 在会话中按 Ctrl-b 后按 d，以保持任务运行并退出窗口
tmux ls
tmux attach -t md2json
tmux capture-pane -pt md2json -S -80
tmux list-panes -t md2json -F '#{pane_pid} #{pane_current_command} #{pane_current_path}'
pgrep -af 'md2json_api.cli convert'
```

中断后续跑必须使用相同输入、相同设置和相同输出目录：

```bash
python3 -m md2json_api.cli convert /data/input/book.md \
  --backend azure \
  --model "$MD2JSON_MODEL" \
  --prompt-profile auto \
  --structure-mode llm \
  --audit-mode llm \
  --resume \
  --out-dir /data/jobs/manual-001/output
```

续跑现在会校验输入内容摘要和有效转换设置，并使用输出目录互斥锁；输入或设置改变时不会复用旧缓存。

## Prompt Profiles

API backend 支持三套 prompt profile，也可以用 `auto` 自动判断：

```bash
cd /home/xuanzhi_ren/Pdf2jsonPipeline/md2json_api_project

set -a
source ~/.config/md2json/azure.env
set +a

python3 -m md2json_api.cli convert /home/xuanzhi_ren/Pdf2jsonPipeline/Test/Convex-analysis-Ralph-Tyrell-Rockafellar-20260521165850/Convex-analysis-Ralph-Tyrell-Rockafellar.md \
  --backend azure \
  --model "$MD2JSON_MODEL" \
  --azure-endpoint "$AZURE_OPENAI_ENDPOINT" \
  --azure-api-version "$AZURE_OPENAI_API_VERSION" \
  --prompt-profile auto \
  --structure-mode llm \
  --audit-mode llm \
  --out-dir /home/xuanzhi_ren/Pdf2jsonPipeline/Test/Convex-analysis-Ralph-Tyrell-Rockafellar-20260521165850/output_json2
```

- `auto`：默认。按 section 内容自动选择 `textbook`、`paper` 或 `chinese_math`。
- `textbook`：适合数学教材/讲义。会保留 section-opening central definition/remark，但避免把普通解释段落都抽成 remark。
- `paper`：适合论文。会忽略 abstract/introduction/related work/experiment prose，重点抽 theorem/lemma/proposition/claim/assumption/algorithm 等正式块。
- `chinese_math`：适合中文数学书。明确识别 `定义`、`定义-定理`、`命题`、`证明`、`注记`、`例` 等，不翻译中文原文。

当前 system prompt 内嵌了：

- ref 风格的逐字段说明：`index`、`label`、`env`、`number_components`、`context`、`content`、`dependencies`、`proof`；
- ref 的核心 extraction rules；
- textbook / paper / chinese_math profile-specific rules；
- 5 个 few-shot examples，覆盖 section-opening prose、theorem+proof、exercise、paper algorithm、中文数学条目。

## 设计说明

1. LLM structure planner：
   - 先由硬 splitter 生成一个 draft，再把候选标题、item/activity 标题、上下文行号交给 LLM。
   - LLM 输出 `chapter`、`chapter_number`、front/back matter ranges、canonical sections、每个 section 的起止行。
   - 识别 `1.1 滤子` 这类非 Markdown 裸标题；避免把 `Definition`、`Example`、`Try it`、`Investigate`、证明内部 `1.` / `F.` 枚举误判为 section。
   - 如果整个文件明显只是某一节的摘录，例如 `1.3 Rules of Logic`，会把 `1.3.1` 等下级教学小标题保留在该 section 内，而不是直接改写成 JSON 的 section context。
2. 硬 splitter fallback：
   - `--structure-mode hard` 或离线 `local` backend 时，仍使用本地规则切分。
   - 支持 `Chapter 1`、`第一章`、`1 Introduction`、`1.1 Problem statement`、`A Proof of ...` 等常见教材/论文标题。
   - 排除 `References`、`Bibliography`、`Acknowledgements` 等非正文块。
3. LLM 初抽：
   - 每个 section 单独调用 API，由 LLM 判断数学条目和 proof 边界。
   - 初抽结果写入 `initial_sections/`。
4. LLM 审计与修复：
   - 第二次按 section 调用 API，对照 `source_md_sections/sectionXX.md` 和初抽 JSON。
   - 输出 ref 风格的 `audit_markdown`、`patch_candidate` 和完整 `repaired_items`。
   - 最终 `sections/` 和总 JSON 使用 repaired items。
5. API 使用 structured JSON schema，强制输出固定字段。
6. 后处理统一覆盖 `context`、`label`、`index`，避免模型 label 漂移。
7. 硬逻辑 quality report 再做一层 sanity check，提示明显漏提、proof 未拆、全 remark、重复 label 等风险。
8. 对于 `Proof of 13.9` 这类延后证明，prompt 要求在同 section 内归属到被证明的原条目；如果目标在别的 section，则保守保留为 remark 并记录显式 dependency。

## CLI 参数

### `convert`

`convert` 是完整转换入口：

```bash
python3 -m md2json_api.cli convert INPUT_MD [options]
```

位置参数：

- `INPUT_MD`：输入 Markdown 文件路径。

可选参数：

- `--out-dir PATH`：输出目录。默认写到输入文件同级的 `<input_stem>_json`。
- `--backend {openai,azure,mock,local}`：后端类型。默认 `openai`。
  - `openai`：调用 OpenAI Responses API。
  - `azure`：调用 Azure OpenAI Chat Completions。
  - `mock`：不联网，写出模拟 API 请求/响应 trace，用于检查 prompt/schema 和链路。
  - `local`：离线规则模式，只适合 smoke test。
- `--model MODEL`：模型名或 Azure deployment 名。默认读取 `MD2JSON_MODEL`，未设置时为 `gpt-5.2`。
- `--api-key KEY`：直接传 API key。一般不推荐写在命令行，优先使用环境变量 `OPENAI_API_KEY` 或 `AZURE_OPENAI_API_KEY`。
- `--base-url URL`：OpenAI-compatible 服务地址。默认读取 `OPENAI_BASE_URL`。
- `--azure-endpoint URL`：Azure OpenAI endpoint。默认读取 `AZURE_OPENAI_ENDPOINT`。`--backend azure` 时必需。
- `--azure-api-version VERSION`：Azure OpenAI API version。默认读取 `AZURE_OPENAI_API_VERSION`，未设置时为 `2024-10-21`。
- `--max-output-tokens N`：限制单个 section 调用的最大输出 token 数。默认不传。
- `--prompt-profile {auto,textbook,paper,chinese_math}`：prompt profile。默认读取 `MD2JSON_PROMPT_PROFILE`，未设置时为 `auto`。
- `--structure-mode {auto,llm,hard}`：是否用 LLM 规划 chapter/section 结构。
  - `auto`：默认；当硬 splitter 发现 synthetic section、裸 numbered heading、heading 层级混乱等可疑结构时，先调用 LLM structure planner。
  - `llm`：只要 backend 支持 API，就总是先调用 LLM structure planner；适合海量 pdf2md 中 heading 层级不稳定的场景。
  - `hard`：禁用 structure planner，只使用本地 splitter。
- `--audit-mode {auto,llm,off}`：是否执行 LLM audit/repair。
  - `auto`：默认；对 `openai`、`azure`、`mock` 启用 LLM audit/repair，对 `local` 不启用。
  - `llm`：显式要求 LLM audit/repair；仅对 `openai`、`azure`、`mock` 生效。
  - `off`：只做初抽，不做 LLM 检查/修复。
- `--resume`：在 `.conversion_manifest.json` 的输入摘要和有效设置校验通过后，复用输出目录中已有的 `structure_api_call/response.json`、`api_calls/sectionXX_response.json` 和 `audit_api_calls/sectionXX_response.json`，用于中断后续跑，避免重复调用已经成功的步骤。旧版本生成但没有 manifest 的 trace 不会被安全续跑复用。

相关环境变量：

- `OPENAI_API_KEY`：OpenAI backend 的 API key。
- `OPENAI_BASE_URL`：OpenAI-compatible base URL，可选。
- `AZURE_OPENAI_API_KEY`：Azure backend 的 API key。
- `AZURE_OPENAI_ENDPOINT`：Azure endpoint。
- `AZURE_OPENAI_API_VERSION`：Azure API version。
- `MD2JSON_MODEL`：默认模型名/deployment 名。
- `MD2JSON_PROMPT_PROFILE`：默认 prompt profile。
- `MD2JSON_STRUCTURE_MODE`：默认 structure mode。
- `MD2JSON_AUDIT_MODE`：默认 audit mode。

CLI 结束时会打印 item/section 数和输出目录。真实 API token usage 会写入 trace 文件：

```text
api_calls/sectionXX.json
audit_api_calls/sectionXX.json
structure_api_call/call.json
```

其中 `usage.prompt_tokens`、`usage.completion_tokens`、`usage.total_tokens` 可用于汇总整次转换消耗。

### `inspect`

`inspect` 只检查 section 切分，不调用 API：

```bash
python3 -m md2json_api.cli inspect INPUT_MD
```

位置参数：

- `INPUT_MD`：输入 Markdown 文件路径。

输出内容包括检测到的 chapter/section、行号、front/back matter 长度和 splitter warnings。

## 质量报告关注点

`quality_report.md` 会提示：

- source 有 theorem-like marker 但提取为空；
- source 有多个 theorem-like marker 但只提取到极少 item；
- 所有 item 都是 `remark`；
- proof marker 留在 `content` 中但没有拆入 `proof`；
- content 出现截断标记；
- duplicate labels；
- splitter 创建 synthetic section 或发现重复 section number。

## 输出校验

```bash
python3 -m json.tool outputs/tiny_local_json/chapter13_tiny.json
python3 -m unittest discover -s tests
```
