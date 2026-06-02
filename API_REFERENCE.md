# md2json API 对接文档

本文档面向前端和调用方，说明已部署的 `md2json-api` HTTP 服务的接口契约、请求参数、响应结构、状态轮询和错误处理方式。

## 1. 服务信息

### 1.1 Base URL

当前部署地址：

```text
http://8.211.159.42/md2json
```

下文所有接口路径均以该 Base URL 为前缀。例如：

```text
GET http://8.211.159.42/md2json/healthz
```

### 1.2 认证方式

除 `GET /healthz` 外，所有业务接口都需要 Bearer Token：

```http
Authorization: Bearer <MD2JSON_API_TOKEN>
```

注意：

- 前端不要把真实 token 写入源码仓库。
- 浏览器端如果不能安全保存 token，建议由业务后端代理调用本服务。
- 请求中不需要、也不允许传 OpenAI、Azure、Doc2X 等第三方 API Key；这些配置由服务端管理员维护。

### 1.3 内容类型

创建任务接口均使用：

```http
Content-Type: multipart/form-data
```

查询任务和获取结果接口使用普通 `GET` / `POST`，返回 JSON、Markdown 或纯文本。

### 1.4 异步任务模型

所有转换任务都是异步任务：

1. 客户端上传文件并创建任务。
2. 服务立即返回 `202 Accepted` 和 `job_id`。
3. 客户端使用 `job_id` 轮询状态接口。
4. 只有当 `status=succeeded` 后，才能调用结果接口。

任务状态：

| status | 含义 |
|---|---|
| `queued` | 已入队，等待后台 worker 处理 |
| `running` | 正在处理 |
| `succeeded` | 处理成功，可以获取结果 |
| `failed` | 处理失败，需要联系服务维护方并提供 `job_id` |

建议前端轮询间隔：

- 普通 Markdown：2-5 秒。
- PDF Doc2X 或 PDF 全流程：5-10 秒。
- 连续失败、网络异常或 5xx 时做退避重试，避免高频请求。

## 2. 能力概览

服务提供四类能力：

| 能力 | 创建任务接口 | 主要输出 |
|---|---|---|
| 统一前端转换入口 | `POST /v1/source-conversions` | `md2json.annotation.v1` 标注系统 JSON、质量摘要、用量统计 |
| Markdown 转最终 JSON | `POST /v1/conversions` | md2json item JSON、质量报告、用量统计 |
| PDF 只执行 Doc2X | `POST /v1/doc2x-conversions` | Doc2X Markdown、Doc2X pages JSON、用量统计 |
| PDF 全流程转换 | `POST /v1/full-conversions` | 最终 md2json item JSON、质量报告、用量统计 |

标注系统前端推荐优先使用统一入口 `/v1/source-conversions`。当前统一入口支持 `.md`、`.pdf`、`.jpg`、`.jpeg`、`.png`。

## 3. 通用接口

### 3.1 健康检查

```http
GET /healthz
```

该接口不需要认证。

响应示例：

```json
{
  "status": "ok"
}
```

curl 示例：

```bash
curl "http://8.211.159.42/md2json/healthz"
```

### 3.2 统一前端转换入口

该入口面向标注系统前端，屏蔽 Markdown-only 和 PDF full conversion 的历史接口差异，统一返回：

```text
md2json.annotation.v1
```

当前支持：

| 文件类型 | 状态 | 处理路径 |
|---|---|---|
| `.md` | 已支持 | Markdown -> md2json -> annotation.v1 |
| `.pdf` | 已支持 | PDF -> Doc2X -> Markdown -> md2json -> annotation.v1 |
| `.jpg/.jpeg/.png` | 已支持 | Image -> Doc2X image OCR -> Markdown -> md2json -> annotation.v1 |

图片路径使用 Doc2X 官方 API v2 异步图片 layout 接口：

```text
POST /api/v2/async/parse/img/layout
GET  /api/v2/parse/img/layout/status?uid=<uid>
```

前端不需要、也不应传入 Doc2X key 或 endpoint。前端仍然只上传 form-data 到 md2json；md2json 后端会读取文件 bytes，并按 Doc2X 官方要求以图片二进制请求体提交给 Doc2X。

#### 3.2.1 创建任务

```http
POST /v1/source-conversions
```

请求类型：`multipart/form-data`

请求参数：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---:|---:|---|---|
| `file` | file | 是 | 无 | 当前支持 `.md`、`.pdf`、`.jpg`、`.jpeg`、`.png` |
| `prompt_profile` | string | 否 | `auto` | md2json prompt 类型 |
| `structure_mode` | string | 否 | `auto` | md2json 结构识别方式 |
| `audit_mode` | string | 否 | `auto` | md2json 审计修复方式 |
| `doc2x_model` | string | 否 | `v3-2026` | PDF 路径使用 |
| `formula_mode` | string | 否 | `normal` | PDF 路径使用 |
| `formula_level` | string | 否 | `0` | PDF 路径使用 |
| `merge_cross_page_forms` | bool | 否 | `false` | PDF 路径使用 |

curl 示例：

```bash
curl -X POST "http://8.211.159.42/md2json/v1/source-conversions" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -F "file=@chapter.md;type=text/markdown" \
  -F "structure_mode=hard" \
  -F "audit_mode=off"
```

成功响应：`202 Accepted`

```json
{
  "job_id": "markdown_0d7f3c9b9d0b4a3f9c8e0f5d6a1b2c3d",
  "status": "queued",
  "phase": "queued",
  "created_at": "2026-06-01T03:12:45.123456+00:00",
  "updated_at": "2026-06-01T03:12:45.123456+00:00",
  "input_name": "chapter.md",
  "source_type": "markdown",
  "preprocess_progress": null,
  "sections_total": null,
  "sections_completed": 0
}
```

`.pdf` 文件返回的 `job_id` 前缀为 `pdf_`，`source_type` 为 `pdf`。
图片文件返回的 `job_id` 前缀为 `image_`，`source_type` 为 `image`。

#### 3.2.2 查询状态

```http
GET /v1/source-conversions/{job_id}
```

响应字段与创建任务响应一致。PDF 和图片任务中 `preprocess_progress` 表示 Doc2X 前置阶段进度；Markdown 任务为 `null`。

#### 3.2.3 获取标注结果

```http
GET /v1/source-conversions/{job_id}/result
```

成功响应为 `md2json.annotation.v1`：

```json
{
  "schema_version": "md2json.annotation.v1",
  "source": {
    "filename": "chapter.md",
    "source_type": "markdown",
    "original_mime_type": "text/markdown",
    "content_hash": null
  },
  "document": {
    "title": "",
    "language": "",
    "chapters": []
  },
  "items": [
    {
      "id": "item_000001",
      "order_index": 1,
      "label": "Definition 1",
      "label_raw": "Definition 1",
      "type": "def",
      "number_components": [],
      "statement": "Definition 1. Value.",
      "proof": "",
      "context": {
        "chapter_title": "",
        "chapter_number": "",
        "section_title": "1 Test",
        "section_number": "1",
        "subsection_title": "",
        "subsection_number": ""
      },
      "dependencies": [],
      "source_refs": {
        "pages": [],
        "block_ids": [],
        "span_ids": [],
        "bbox_refs": []
      },
      "assets": {
        "image_path": "",
        "caption": "",
        "table_markdown": ""
      },
      "audit": {
        "modified": false,
        "issues": []
      },
      "raw": {
        "env": "def"
      }
    }
  ],
  "quality": {
    "error_count": 0,
    "warning_count": 0,
    "issues": []
  }
}
```

#### 3.2.4 获取质量摘要和用量

```http
GET /v1/source-conversions/{job_id}/quality
GET /v1/source-conversions/{job_id}/usage
```

`/quality` 返回结果中的 `quality` 对象，包含：

- `error_count`
- `warning_count`
- `issues[]`

`issues[].item_id` 用于前端从问题列表跳转到对应 item。无法归属到单个 item 的整体问题可以返回 `item_id=null`。

## 4. Markdown 转最终 JSON

该流程适用于前端或业务后端已经有 Markdown 文件，只需要抽取数学定义、定理、引理、命题、例题、练习、证明等结构化条目的场景。

### 4.1 创建 Markdown 转换任务

```http
POST /v1/conversions
```

请求类型：`multipart/form-data`

请求参数：

| 字段 | 类型 | 必填 | 默认值 | 可选值 | 说明 |
|---|---:|---:|---|---|---|
| `file` | file | 是 | 无 | `.md` 文件 | 上传的 Markdown 源文件 |
| `prompt_profile` | string | 否 | `auto` | `auto`, `textbook`, `paper`, `chinese_math` | 抽取 prompt 类型 |
| `structure_mode` | string | 否 | `auto` | `auto`, `llm`, `hard` | 章节结构识别方式 |
| `audit_mode` | string | 否 | `auto` | `auto`, `llm`, `off` | 是否执行 LLM 审计修复 |

参数说明：

| 参数 | 说明 |
|---|---|
| `prompt_profile=auto` | 自动判断教材、论文或中文数学文本，推荐默认使用 |
| `prompt_profile=textbook` | 适合教材、讲义、书籍章节 |
| `prompt_profile=paper` | 适合论文，重点抽 theorem/lemma/proposition/claim/algorithm 等正式块 |
| `prompt_profile=chinese_math` | 适合中文数学书，识别“定义”“定理”“命题”“证明”“注记”“例”等 |
| `structure_mode=auto` | 自动判断是否需要 LLM 规划章节结构，推荐默认使用 |
| `structure_mode=llm` | 强制使用 LLM 规划章节结构，适合 PDF 转 Markdown 后 heading 不稳定的文本 |
| `structure_mode=hard` | 只使用本地规则切分章节，速度更快但容错更弱 |
| `audit_mode=auto` | 默认策略，正式后端通常会启用审计修复 |
| `audit_mode=llm` | 强制执行 LLM 审计修复 |
| `audit_mode=off` | 关闭审计修复，速度更快但质量风险更高 |

文件限制：

- 只接受扩展名为 `.md` 的文件。
- 空文件会返回 `400`。
- 超过服务端上传大小限制会返回 `413`。

请求示例：

```bash
curl -X POST "http://8.211.159.42/md2json/v1/conversions" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -F "file=@chapter13_tiny.md;type=text/markdown" \
  -F "prompt_profile=auto" \
  -F "structure_mode=llm" \
  -F "audit_mode=llm"
```

成功响应：`202 Accepted`

```json
{
  "job_id": "0d7f3c9b9d0b4a3f9c8e0f5d6a1b2c3d",
  "status": "queued",
  "phase": "queued",
  "created_at": "2026-06-01T03:12:45.123456+00:00",
  "updated_at": "2026-06-01T03:12:45.123456+00:00",
  "input_name": "chapter13_tiny.md",
  "sections_total": null,
  "sections_completed": 0
}
```

### 4.2 查询 Markdown 转换任务状态

```http
GET /v1/conversions/{job_id}
```

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/conversions/<JOB_ID>" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>"
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` | string | 任务 ID |
| `status` | string | `queued`, `running`, `succeeded`, `failed` |
| `phase` | string | 当前内部阶段，例如 `queued`, `starting`, `completed`, `failed` |
| `created_at` | string | ISO 8601 UTC 时间 |
| `updated_at` | string | ISO 8601 UTC 时间 |
| `input_name` | string | 上传文件名 |
| `sections_total` | integer/null | 识别出的 section 总数，尚未识别时为 `null` |
| `sections_completed` | integer | 已完成 section 数 |
| `error` | string | 仅 `status=failed` 时返回的脱敏错误提示 |

运行中响应示例：

```json
{
  "job_id": "0d7f3c9b9d0b4a3f9c8e0f5d6a1b2c3d",
  "status": "running",
  "phase": "extracting",
  "created_at": "2026-06-01T03:12:45.123456+00:00",
  "updated_at": "2026-06-01T03:13:10.234567+00:00",
  "input_name": "chapter13_tiny.md",
  "sections_total": 12,
  "sections_completed": 4
}
```

失败响应示例：

```json
{
  "job_id": "0d7f3c9b9d0b4a3f9c8e0f5d6a1b2c3d",
  "status": "failed",
  "phase": "failed",
  "created_at": "2026-06-01T03:12:45.123456+00:00",
  "updated_at": "2026-06-01T03:20:00.000000+00:00",
  "input_name": "chapter13_tiny.md",
  "sections_total": 12,
  "sections_completed": 4,
  "error": "Conversion failed. Contact the service operator with the job_id."
}
```

### 4.3 重新排队失败任务

```http
POST /v1/conversions/{job_id}/resume
```

该接口仅支持 Markdown 转最终 JSON 任务，并且只能用于 `status=failed` 的任务。

请求示例：

```bash
curl -X POST "http://8.211.159.42/md2json/v1/conversions/<JOB_ID>/resume" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>"
```

成功响应：`202 Accepted`

响应结构与状态接口一致。

常见错误：

- 任务不存在：`404`
- 当前任务不是 `failed`：`409`

### 4.4 获取最终 JSON

```http
GET /v1/conversions/{job_id}/result
```

只有 `status=succeeded` 后才能调用。

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/conversions/<JOB_ID>/result" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -o result.json
```

响应类型：

```http
Content-Type: application/json
```

响应主体是 item 数组：

```json
[
  {
    "index": 1,
    "label": "Definition 13.1",
    "env": "def",
    "number_components": ["13", "1"],
    "context": {
      "chapter": "Chapter 13. First order differentiation",
      "section": "13.A. Ultratangent space",
      "chapter_number": "13",
      "section_number": "13.A"
    },
    "content": "Definition 13.1. ...",
    "dependencies": [],
    "proof": null
  },
  {
    "index": 2,
    "label": "Theorem 13.2",
    "env": "thm",
    "number_components": ["13", "2"],
    "context": {
      "chapter": "Chapter 13. First order differentiation",
      "section": "13.A. Ultratangent space",
      "chapter_number": "13",
      "section_number": "13.A"
    },
    "content": "Theorem 13.2. ...",
    "dependencies": ["Definition 13.1"],
    "proof": "Proof. ..."
  }
]
```

item 字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `index` | integer | 全文顺序编号，从 1 开始 |
| `label` | string | 规范化后的条目标签，例如 `Theorem 1.2` |
| `env` | string | 条目类型 |
| `number_components` | string[] | 从编号中解析出的组成部分 |
| `context.chapter` | string | 所属 chapter 名称 |
| `context.section` | string | 所属 section 名称 |
| `context.chapter_number` | string | chapter 编号，无法识别时可能为空字符串 |
| `context.section_number` | string | section 编号，无法识别时可能为空字符串 |
| `content` | string | 条目正文，不含独立拆出的证明 |
| `dependencies` | string[] | 依赖的其他条目标签 |
| `proof` | string/null | 证明文本；没有证明或不适用时为 `null` |

`env` 可选值：

```text
def, thm, prop, lemma, cor, remark, example, exercise, algorithm,
assumption, claim, conjecture, problem, question, notation
```

### 4.5 获取质量报告

```http
GET /v1/conversions/{job_id}/quality
```

只有 `status=succeeded` 后才能调用。

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/conversions/<JOB_ID>/quality" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -o quality.json
```

响应示例：

```json
{
  "source_file": "chapter13_tiny.md",
  "items_total": 18,
  "sections_total": 4,
  "warnings": [],
  "duplicate_labels": [],
  "empty_sections": []
}
```

质量报告的具体字段可能随服务端质量检查规则扩展。前端建议：

- 展示 `source_file`、item 总数、section 总数。
- 如果存在 `warnings`、`duplicate_labels`、`empty_sections` 等非空字段，提示用户结果可能需要人工复核。
- 不要假设质量报告只有上述字段。

### 4.6 获取用量统计

```http
GET /v1/conversions/{job_id}/usage
```

只有 `status=succeeded` 后才能调用。

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/conversions/<JOB_ID>/usage" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -o usage.json
```

响应示例：

```json
{
  "requests": 9,
  "input_tokens": 120000,
  "output_tokens": 24000,
  "total_tokens": 144000,
  "llm_elapsed_seconds": 180.25,
  "wall_clock_elapsed_seconds": 220.67,
  "phases": {
    "structure": {
      "requests": 1,
      "input_tokens": 10000,
      "output_tokens": 2000,
      "total_tokens": 12000,
      "elapsed_seconds": 20.5
    },
    "extract": {
      "requests": 4,
      "input_tokens": 70000,
      "output_tokens": 12000,
      "total_tokens": 82000,
      "elapsed_seconds": 90.3
    },
    "audit": {
      "requests": 4,
      "input_tokens": 40000,
      "output_tokens": 10000,
      "total_tokens": 50000,
      "elapsed_seconds": 69.45
    }
  }
}
```

用量字段由后端汇总，可能因不同后端或任务路径略有差异。前端应以存在字段为准。

## 5. PDF 只执行 Doc2X

该流程适用于只需要把 PDF 识别成 Doc2X Markdown 和 Doc2X pages JSON，不执行 md2json 结构化抽取的场景。

### 5.1 创建 Doc2X 转换任务

```http
POST /v1/doc2x-conversions
```

请求类型：`multipart/form-data`

请求参数：

| 字段 | 类型 | 必填 | 默认值 | 可选值 | 说明 |
|---|---:|---:|---|---|---|
| `file` | file | 是 | 无 | `.pdf` 文件 | 上传的 PDF 源文件 |
| `doc2x_model` | string | 否 | `v3-2026` | `v2`, `v3-2026` | Doc2X 模型版本 |
| `formula_mode` | string | 否 | `normal` | `normal`, `dollar` | 公式输出模式 |
| `formula_level` | string/integer | 否 | `0` | `0`, `1`, `2` | 公式识别级别 |
| `merge_cross_page_forms` | boolean | 否 | `false` | `true`, `false` | 是否合并跨页表格 |

文件限制：

- 只接受扩展名为 `.pdf` 的文件。
- 空文件会返回 `400`。
- 超过服务端 Doc2X 上传大小限制会返回 `413`。

请求示例：

```bash
curl -X POST "http://8.211.159.42/md2json/v1/doc2x-conversions" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -F "file=@book.pdf;type=application/pdf" \
  -F "doc2x_model=v3-2026" \
  -F "formula_mode=normal" \
  -F "formula_level=0" \
  -F "merge_cross_page_forms=false"
```

成功响应：`202 Accepted`

```json
{
  "job_id": "6bfe54b2f5b7402a82e4c9c3df2a6a91",
  "status": "queued",
  "phase": "queued",
  "created_at": "2026-06-01T03:12:45.123456+00:00",
  "updated_at": "2026-06-01T03:12:45.123456+00:00",
  "input_name": "book.pdf",
  "progress": null
}
```

### 5.2 查询 Doc2X 任务状态

```http
GET /v1/doc2x-conversions/{job_id}
```

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/doc2x-conversions/<JOB_ID>" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>"
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` | string | 任务 ID |
| `status` | string | `queued`, `running`, `succeeded`, `failed` |
| `phase` | string | 当前阶段，例如 `saving_input`, `waiting_parse`, `finalizing`, `completed` |
| `created_at` | string | ISO 8601 UTC 时间 |
| `updated_at` | string | ISO 8601 UTC 时间 |
| `input_name` | string | 上传文件名 |
| `progress` | integer/null | Doc2X 进度百分比，可能为 `null` |
| `error` | string | 仅 `status=failed` 时返回 |

响应示例：

```json
{
  "job_id": "6bfe54b2f5b7402a82e4c9c3df2a6a91",
  "status": "running",
  "phase": "waiting_parse",
  "created_at": "2026-06-01T03:12:45.123456+00:00",
  "updated_at": "2026-06-01T03:13:20.000000+00:00",
  "input_name": "book.pdf",
  "progress": 50
}
```

### 5.3 获取 Doc2X Markdown

```http
GET /v1/doc2x-conversions/{job_id}/markdown
```

只有 `status=succeeded` 后才能调用。

响应类型：

```http
Content-Type: text/markdown; charset=utf-8
```

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/doc2x-conversions/<JOB_ID>/markdown" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -o doc2x-output.md
```

### 5.4 获取 Doc2X pages JSON

```http
GET /v1/doc2x-conversions/{job_id}/json
```

只有 `status=succeeded` 后才能调用。

响应类型：

```http
Content-Type: application/json
```

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/doc2x-conversions/<JOB_ID>/json" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -o doc2x-pages.json
```

响应示例：

```json
{
  "pages": [
    {
      "page_idx": 0,
      "md": "## 1 Test\n\nDefinition 1. From PDF.\n"
    }
  ]
}
```

Doc2X JSON 的具体结构由 Doc2X 输出决定。前端不应假设只有 `pages[].page_idx` 和 `pages[].md` 两个字段。

### 5.5 获取 Doc2X 用量统计

```http
GET /v1/doc2x-conversions/{job_id}/usage
```

只有 `status=succeeded` 后才能调用。

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/doc2x-conversions/<JOB_ID>/usage" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -o doc2x-usage.json
```

响应示例：

```json
{
  "requests": 1,
  "input_tokens": 0,
  "output_tokens": 0,
  "total_tokens": 0,
  "llm_elapsed_seconds": 42.123456,
  "wall_clock_elapsed_seconds": 42.123456,
  "phases": {
    "doc2x": {
      "requests": 1,
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0,
      "elapsed_seconds": 42.123456
    }
  }
}
```

## 6. PDF 全流程转换

该流程适用于上传 PDF 后直接得到最终 md2json item JSON 的场景。服务端会自动执行：

```text
PDF -> Doc2X Markdown/JSON -> md2json 最终 JSON
```

### 6.1 创建 PDF 全流程任务

```http
POST /v1/full-conversions
```

请求类型：`multipart/form-data`

请求参数：

| 字段 | 类型 | 必填 | 默认值 | 可选值 | 说明 |
|---|---:|---:|---|---|---|
| `file` | file | 是 | 无 | `.pdf` 文件 | 上传的 PDF 源文件 |
| `doc2x_model` | string | 否 | `v3-2026` | `v2`, `v3-2026` | Doc2X 模型版本 |
| `formula_mode` | string | 否 | `normal` | `normal`, `dollar` | 公式输出模式 |
| `formula_level` | string/integer | 否 | `0` | `0`, `1`, `2` | 公式识别级别 |
| `merge_cross_page_forms` | boolean | 否 | `false` | `true`, `false` | 是否合并跨页表格 |
| `prompt_profile` | string | 否 | `auto` | `auto`, `textbook`, `paper`, `chinese_math` | md2json prompt 类型 |
| `structure_mode` | string | 否 | `auto` | `auto`, `llm`, `hard` | md2json 章节结构识别方式 |
| `audit_mode` | string | 否 | `auto` | `auto`, `llm`, `off` | md2json 审计修复方式 |

请求示例：

```bash
curl -X POST "http://8.211.159.42/md2json/v1/full-conversions" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -F "file=@book.pdf;type=application/pdf" \
  -F "doc2x_model=v3-2026" \
  -F "formula_mode=normal" \
  -F "formula_level=0" \
  -F "merge_cross_page_forms=false" \
  -F "prompt_profile=auto" \
  -F "structure_mode=llm" \
  -F "audit_mode=llm"
```

成功响应：`202 Accepted`

```json
{
  "job_id": "f8919a551a824c769b8f4fcf7b4b63c2",
  "status": "queued",
  "phase": "queued",
  "created_at": "2026-06-01T03:12:45.123456+00:00",
  "updated_at": "2026-06-01T03:12:45.123456+00:00",
  "input_name": "book.pdf",
  "doc2x_progress": null,
  "sections_total": null,
  "sections_completed": 0
}
```

### 6.2 查询 PDF 全流程任务状态

```http
GET /v1/full-conversions/{job_id}
```

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/full-conversions/<JOB_ID>" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>"
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` | string | 任务 ID |
| `status` | string | `queued`, `running`, `succeeded`, `failed` |
| `phase` | string | 当前阶段，Doc2X 阶段通常以 `doc2x_` 开头，md2json 阶段通常以 `md2json_` 开头 |
| `created_at` | string | ISO 8601 UTC 时间 |
| `updated_at` | string | ISO 8601 UTC 时间 |
| `input_name` | string | 上传文件名 |
| `doc2x_progress` | integer/null | Doc2X 进度百分比 |
| `sections_total` | integer/null | md2json section 总数 |
| `sections_completed` | integer | md2json 已完成 section 数 |
| `error` | string | 仅 `status=failed` 时返回 |

响应示例：

```json
{
  "job_id": "f8919a551a824c769b8f4fcf7b4b63c2",
  "status": "running",
  "phase": "md2json_extracting",
  "created_at": "2026-06-01T03:12:45.123456+00:00",
  "updated_at": "2026-06-01T03:16:01.000000+00:00",
  "input_name": "book.pdf",
  "doc2x_progress": 100,
  "sections_total": 20,
  "sections_completed": 7
}
```

### 6.3 获取最终 JSON

```http
GET /v1/full-conversions/{job_id}/result
```

只有 `status=succeeded` 后才能调用。

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/full-conversions/<JOB_ID>/result" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -o full-result.json
```

响应结构与 `GET /v1/conversions/{job_id}/result` 相同，均为 item 数组。

### 6.4 获取质量报告

```http
GET /v1/full-conversions/{job_id}/quality
```

只有 `status=succeeded` 后才能调用。

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/full-conversions/<JOB_ID>/quality" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -o full-quality.json
```

响应结构与 `GET /v1/conversions/{job_id}/quality` 类似，其中 `source_file` 为原始 PDF 文件名。

### 6.5 获取全流程用量统计

```http
GET /v1/full-conversions/{job_id}/usage
```

只有 `status=succeeded` 后才能调用。

请求示例：

```bash
curl "http://8.211.159.42/md2json/v1/full-conversions/<JOB_ID>/usage" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -o full-usage.json
```

响应示例：

```json
{
  "requests": 10,
  "input_tokens": 120000,
  "output_tokens": 24000,
  "total_tokens": 144000,
  "llm_elapsed_seconds": 180.25,
  "wall_clock_elapsed_seconds": 285.33,
  "phases": {
    "doc2x": {
      "requests": 1,
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0,
      "elapsed_seconds": 65.08
    },
    "md2json": {
      "requests": 9,
      "input_tokens": 120000,
      "output_tokens": 24000,
      "total_tokens": 144000,
      "llm_elapsed_seconds": 180.25,
      "wall_clock_elapsed_seconds": 220.25
    }
  }
}
```

## 7. 错误响应

FastAPI 标准错误响应格式：

```json
{
  "detail": "错误说明"
}
```

常见 HTTP 状态码：

| 状态码 | 场景 | 示例 detail |
|---:|---|---|
| `400` | 文件类型错误、空文件、参数值不支持 | `Only .pdf files are accepted.` |
| `401` | 缺少或错误的 Bearer Token | `Bearer token required.` / `Invalid bearer token.` |
| `404` | `job_id` 不存在 | `Job not found.` |
| `409` | 任务未完成时获取结果，或不可 resume | `Job is not complete: running.` |
| `413` | 上传文件超过服务端限制 | `Upload is too large.` |
| `500` | 服务端异常 | 由网关或服务端返回 |

前端处理建议：

- `401`：提示登录态或服务 token 异常，不要自动重试。
- `400` / `413`：提示用户更换文件或调整参数。
- `404`：提示任务不存在或已被清理。
- `409`：继续轮询状态，不应当当作失败。
- `5xx`：使用指数退避重试，并允许用户稍后再查。

## 8. 前端集成建议

### 8.1 推荐的任务流程

```text
上传文件 -> 创建任务 -> 保存 job_id -> 轮询状态 -> succeeded 后拉取结果/质量报告/用量统计
```

前端应把 `job_id` 作为任务详情页或本地任务列表的核心标识。页面刷新后，只要仍有 `job_id`，就可以继续查询任务状态。

### 8.2 进度展示

Markdown 转换：

- 有 `sections_total` 时，可展示 `sections_completed / sections_total`。
- `sections_total=null` 时，展示当前 `phase` 或“不确定进度”。

Doc2X 转换：

- 优先展示 `progress`。
- `progress=null` 时，展示当前 `phase`。

PDF 全流程：

- Doc2X 阶段展示 `doc2x_progress`。
- md2json 阶段展示 `sections_completed / sections_total`。
- 可以根据 `phase` 是否以 `doc2x_` 或 `md2json_` 开头区分阶段。

### 8.3 结果下载

建议提供以下下载入口：

| 流程 | 建议下载项 |
|---|---|
| Markdown 转最终 JSON | `result.json`, `quality.json`, `usage.json` |
| PDF 只执行 Doc2X | `doc2x-output.md`, `doc2x-pages.json`, `usage.json` |
| PDF 全流程 | `full-result.json`, `full-quality.json`, `full-usage.json` |

### 8.4 安全注意事项

- 不要在浏览器控制台、日志、错误上报中记录完整 `Authorization` header。
- 不要把上传文件内容、转换结果或质量报告发送到第三方日志系统，除非业务上明确允许。
- 服务端不会通过公开 API 返回内部路径、trace 文件、第三方临时签名 URL 或真实错误栈。

## 9. JavaScript 调用示例

以下示例演示浏览器或 Node.js 中使用 `fetch` 创建 PDF 全流程任务并轮询结果。生产环境中 token 建议由业务后端代理，不建议长期暴露在浏览器端。

```js
const BASE_URL = "http://8.211.159.42/md2json";
const TOKEN = "<MD2JSON_API_TOKEN>";

async function createFullConversion(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("doc2x_model", "v3-2026");
  form.append("formula_mode", "normal");
  form.append("formula_level", "0");
  form.append("merge_cross_page_forms", "false");
  form.append("prompt_profile", "auto");
  form.append("structure_mode", "llm");
  form.append("audit_mode", "llm");

  const response = await fetch(`${BASE_URL}/v1/full-conversions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOKEN}`,
    },
    body: form,
  });

  if (!response.ok) {
    throw new Error(`Create task failed: ${response.status} ${await response.text()}`);
  }

  return response.json();
}

async function getFullConversionStatus(jobId) {
  const response = await fetch(`${BASE_URL}/v1/full-conversions/${jobId}`, {
    headers: {
      Authorization: `Bearer ${TOKEN}`,
    },
  });

  if (!response.ok) {
    throw new Error(`Query task failed: ${response.status} ${await response.text()}`);
  }

  return response.json();
}

async function getFullConversionResult(jobId) {
  const response = await fetch(`${BASE_URL}/v1/full-conversions/${jobId}/result`, {
    headers: {
      Authorization: `Bearer ${TOKEN}`,
    },
  });

  if (!response.ok) {
    throw new Error(`Get result failed: ${response.status} ${await response.text()}`);
  }

  return response.json();
}
```

## 10. PowerShell 示例

```powershell
$TOKEN = "<MD2JSON_API_TOKEN>"
$BASE = "http://8.211.159.42/md2json"
$FILE = "C:\path\to\book.pdf"

curl.exe -X POST "$BASE/v1/doc2x-conversions" `
  -H "Authorization: Bearer $TOKEN" `
  -F "file=@$FILE;type=application/pdf" `
  -F "doc2x_model=v3-2026" `
  -F "formula_mode=normal" `
  -F "formula_level=0" `
  -F "merge_cross_page_forms=false"
```

查询任务：

```powershell
curl.exe "$BASE/v1/doc2x-conversions/<JOB_ID>" `
  -H "Authorization: Bearer $TOKEN"
```

下载 Markdown：

```powershell
curl.exe "$BASE/v1/doc2x-conversions/<JOB_ID>/markdown" `
  -H "Authorization: Bearer $TOKEN" `
  -o "C:\path\to\doc2x-output.md"
```

下载 Doc2X JSON：

```powershell
curl.exe "$BASE/v1/doc2x-conversions/<JOB_ID>/json" `
  -H "Authorization: Bearer $TOKEN" `
  -o "C:\path\to\doc2x-pages.json"
```

## 11. 接口清单

| Method | Path | 认证 | 说明 |
|---|---|---:|---|
| `GET` | `/healthz` | 否 | 健康检查 |
| `POST` | `/v1/source-conversions` | 是 | 创建统一前端转换任务，当前支持 `.md`、`.pdf` |
| `GET` | `/v1/source-conversions/{job_id}` | 是 | 查询统一前端转换任务状态 |
| `GET` | `/v1/source-conversions/{job_id}/result` | 是 | 获取 `md2json.annotation.v1` 标注结果 |
| `GET` | `/v1/source-conversions/{job_id}/quality` | 是 | 获取 annotation 质量摘要 |
| `GET` | `/v1/source-conversions/{job_id}/usage` | 是 | 获取统一前端转换任务用量统计 |
| `POST` | `/v1/conversions` | 是 | 创建 Markdown 转最终 JSON 任务 |
| `GET` | `/v1/conversions/{job_id}` | 是 | 查询 Markdown 任务状态 |
| `POST` | `/v1/conversions/{job_id}/resume` | 是 | 重新排队失败的 Markdown 任务 |
| `GET` | `/v1/conversions/{job_id}/result` | 是 | 获取 Markdown 任务最终 JSON |
| `GET` | `/v1/conversions/{job_id}/quality` | 是 | 获取 Markdown 任务质量报告 |
| `GET` | `/v1/conversions/{job_id}/usage` | 是 | 获取 Markdown 任务用量统计 |
| `POST` | `/v1/doc2x-conversions` | 是 | 创建 PDF 到 Doc2X 任务 |
| `GET` | `/v1/doc2x-conversions/{job_id}` | 是 | 查询 Doc2X 任务状态 |
| `GET` | `/v1/doc2x-conversions/{job_id}/markdown` | 是 | 获取 Doc2X Markdown |
| `GET` | `/v1/doc2x-conversions/{job_id}/json` | 是 | 获取 Doc2X pages JSON |
| `GET` | `/v1/doc2x-conversions/{job_id}/usage` | 是 | 获取 Doc2X 用量统计 |
| `POST` | `/v1/full-conversions` | 是 | 创建 PDF 全流程任务 |
| `GET` | `/v1/full-conversions/{job_id}` | 是 | 查询 PDF 全流程任务状态 |
| `GET` | `/v1/full-conversions/{job_id}/result` | 是 | 获取 PDF 全流程最终 JSON |
| `GET` | `/v1/full-conversions/{job_id}/quality` | 是 | 获取 PDF 全流程质量报告 |
| `GET` | `/v1/full-conversions/{job_id}/usage` | 是 | 获取 PDF 全流程用量统计 |
