# md2json API 手册

本文档面向前端和调用中间层，提供当前正式 API 对接口径。标注系统前端使用 `/v1/source-conversions` 完成上传、轮询、结果读取和人工标注结果保存。

更新时间：2026-06-04

## 1. 总览

当前推荐能力：

| 能力 | 入口 | 输出 |
|---|---|---|
| 多格式上传并转换为标注 JSON | `/v1/source-conversions` | `md2json.annotation.v1` |
| 保存人工标注修改结果 | `/v1/source-conversions/{job_id}/annotation` | `md2json.annotation.v1` |

当前统一入口支持的文件类型：

| 文件类型 | 处理路径 |
|---|---|
| `.md` | Markdown -> md2json -> annotation JSON |
| `.pdf` | PDF -> Doc2X -> Markdown -> md2json -> annotation JSON |
| `.jpg` / `.jpeg` / `.png` | Image -> Doc2X image OCR -> Markdown -> md2json -> annotation JSON |

暂不支持：

```text
.docx
.tex
.latex
.webp
.bmp
.tiff
```

## 2. 基础配置

当前部署地址：

```text
MD2JSON_BASE_URL=http://8.211.159.42/md2json
```

请求头：

```http
Authorization: Bearer <MD2JSON_API_TOKEN>
```

说明：

- 除 `/healthz` 外，所有业务接口都需要 `Authorization`。
- `MD2JSON_API_TOKEN` 是 md2json 服务自己的访问令牌，作用类似 Runner 服务里的 `RUNNER_AUTHORIZATION`。
- 它不是 OpenAI、Azure OpenAI 或 Doc2X 的第三方 API Key。
- 前端或前端中间层不需要传 OpenAI、Azure、Doc2X key。

推荐调用架构：

```text
浏览器 -> 前端/业务后端或网关 -> md2json
```

如果浏览器直接请求 md2json，需要前端团队自行确认 CORS、HTTPS/mixed content 和 token 暴露风险。md2json 当前交付公网 API 和 token；浏览器跨域、代理和域名网关由前端团队按其部署环境处理。

## 3. 健康检查

```http
GET ${MD2JSON_BASE_URL}/healthz
```

不需要认证。

响应：

```json
{
  "status": "ok"
}
```

## 4. 统一转换服务

### 4.1 创建任务

```http
POST ${MD2JSON_BASE_URL}/v1/source-conversions
Content-Type: multipart/form-data
Authorization: Bearer <MD2JSON_API_TOKEN>
```

请求参数：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---:|---:|---|---|
| `file` | file | 是 | 无 | `.md`、`.pdf`、`.jpg`、`.jpeg`、`.png` |
| `prompt_profile` | string | 否 | `auto` | `auto`、`textbook`、`paper`、`chinese_math` |
| `structure_mode` | string | 否 | `auto` | `auto`、`llm`、`hard` |
| `audit_mode` | string | 否 | `auto` | `auto`、`llm`、`off` |
| `doc2x_model` | string | 否 | `v3-2026` | PDF 路径使用 |
| `formula_mode` | string | 否 | `normal` | PDF 路径使用，`normal` 或 `dollar` |
| `formula_level` | string | 否 | `0` | PDF 路径使用，`0`、`1`、`2` |
| `merge_cross_page_forms` | bool | 否 | `false` | PDF 路径使用 |

图片路径不向前端暴露额外 Doc2X 参数。服务端按 Doc2X 官方图片 OCR 接口要求，以图片二进制请求体调用 Doc2X。

示例：

```bash
curl -X POST "$MD2JSON_BASE_URL/v1/source-conversions" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -F "file=@chapter.md;type=text/markdown" \
  -F "prompt_profile=auto" \
  -F "structure_mode=auto" \
  -F "audit_mode=auto"
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

`source_type` 可选值：

```text
markdown
pdf
image
```

`job_id` 前缀：

| source_type | job_id 前缀 |
|---|---|
| `markdown` | `markdown_` |
| `pdf` | `pdf_` |
| `image` | `image_` |

### 4.2 查询任务

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}
Authorization: Bearer <MD2JSON_API_TOKEN>
```

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` | string | 统一入口任务 ID |
| `status` | string | `queued`、`running`、`succeeded`、`failed` |
| `phase` | string | 当前阶段 |
| `created_at` | string | UTC ISO 时间 |
| `updated_at` | string | UTC ISO 时间 |
| `input_name` | string | 上传文件名 |
| `source_type` | string | `markdown`、`pdf`、`image` |
| `preprocess_progress` | integer/null | PDF/image 前置 OCR 进度；Markdown 为 `null` |
| `sections_total` | integer/null | md2json section 总数 |
| `sections_completed` | integer | 已完成 section 数 |
| `error` | string | 仅失败时返回脱敏错误提示 |

任务状态：

| status | 含义 |
|---|---|
| `queued` | 已入队 |
| `running` | 正在处理 |
| `succeeded` | 成功，可以获取结果 |
| `failed` | 失败，需要提供 `job_id` 给服务维护方排查 |

建议轮询间隔：

- Markdown：2-5 秒。
- PDF / 图片：5-10 秒。
- 5xx 或网络异常时做退避重试。

### 4.3 获取结果

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/result
Authorization: Bearer <MD2JSON_API_TOKEN>
```

只有 `status=succeeded` 后才能调用。任务未完成时返回 `409`。如果该任务已经保存过人工标注结果，本接口返回已保存版本；否则返回 pipeline 自动生成版本。

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

前端第一版建议只依赖这些字段：

| 字段 | 用途 |
|---|---|
| `schema_version` | 判断兼容版本 |
| `source.filename` | 展示源文件名 |
| `source.source_type` | 区分 Markdown、PDF、图片 |
| `items[].id` | block 稳定主键 |
| `items[].order_index` | block 展示顺序 |
| `items[].type` | block 类型 |
| `items[].label` | 条目编号或名称 |
| `items[].statement` | 可编辑正文 |
| `items[].proof` | 可编辑证明 |
| `items[].dependencies` | 依赖/引用列表 |
| `items[].source_refs.pages` | 点击 block 后跳转原文页 |
| `items[].source_refs.bbox_refs` | 后续原文区域高亮 |
| `items[].assets` | 图片、表格、caption |
| `items[].audit.issues` | item 级错误/警告 |
| `quality.error_count` | 全局错误数 |
| `quality.warning_count` | 全局警告数 |
| `quality.issues[]` | 可跳转问题列表 |

`type` 常见值：

```text
def, thm, prop, lemma, cor, remark, example, exercise, algorithm,
assumption, claim, conjecture, problem, question, notation,
heading, paragraph, figure, table, unknown
```

### 4.4 保存标注结果

前端完成人工编辑后，提交完整的 `md2json.annotation.v1` 文档。

```http
PUT ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/annotation
Content-Type: application/json
Authorization: Bearer <MD2JSON_API_TOKEN>
```

请求体：

```json
{
  "schema_version": "md2json.annotation.v1",
  "source": {
    "filename": "chapter.md",
    "source_type": "markdown"
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
      "type": "def",
      "label": "Definition 1",
      "statement": "Edited definition text.",
      "proof": "",
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
        "modified": true,
        "issues": []
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

服务端校验：

| 校验项 | 规则 |
|---|---|
| `schema_version` | 必须是 `md2json.annotation.v1` |
| `source.source_type` | 如果提供，必须和任务的 `source_type` 一致 |
| `items` | 必须是数组 |
| `items[].id` | 必须非空，且同一文档内不重复 |

成功响应：

```json
{
  "job_id": "markdown_0d7f3c9b9d0b4a3f9c8e0f5d6a1b2c3d",
  "schema_version": "md2json.annotation.v1",
  "item_count": 1,
  "saved": true
}
```

### 4.5 获取已保存标注结果

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/annotation
Authorization: Bearer <MD2JSON_API_TOKEN>
```

返回最近一次保存的完整 `md2json.annotation.v1` 文档。如果尚未保存过人工标注结果，返回 `404`。

### 4.6 获取质量摘要

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/quality
Authorization: Bearer <MD2JSON_API_TOKEN>
```

响应是当前 `/result` 中的 `quality` 对象。若已经保存过人工标注结果，则返回已保存版本中的 `quality`。

```json
{
  "error_count": 0,
  "warning_count": 1,
  "issues": [
    {
      "severity": "warning",
      "code": "unresolved_dependency",
      "message": "引用 Lemma 1.2，但未找到对应条目",
      "item_id": "item_000001"
    }
  ]
}
```

`items[].audit` 和根级 `quality` 不重复：

- `items[].audit.issues` 是单个 item 的问题。
- `quality.issues` 是整篇文档的问题列表，尽量通过 `item_id` 指向具体 item。

### 4.7 获取用量统计

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/usage
Authorization: Bearer <MD2JSON_API_TOKEN>
```

响应字段可能随后端和任务路径变化，前端不要强绑定所有字段。常见结构：

```json
{
  "requests": 3,
  "input_tokens": 1000,
  "output_tokens": 500,
  "total_tokens": 1500,
  "wall_clock_elapsed_seconds": 30.5,
  "phases": {}
}
```

## 5. 错误码

错误响应通常为：

```json
{
  "detail": "错误说明"
}
```

常见状态码：

| 状态码 | 场景 |
|---:|---|
| `400` | 文件类型不支持、参数值不支持、空文件 |
| `401` | 缺少或错误的 `Authorization` |
| `404` | `job_id` 不存在 |
| `409` | 任务未完成时获取结果 |
| `413` | 上传文件超过服务端限制 |
| `500` | 服务端异常 |

## 6. JavaScript 示例

```js
const BASE_URL = "http://8.211.159.42/md2json";
const AUTHORIZATION = "Bearer <MD2JSON_API_TOKEN>";

export async function createSourceConversion(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("prompt_profile", "auto");
  form.append("structure_mode", "auto");
  form.append("audit_mode", "auto");

  const response = await fetch(`${BASE_URL}/v1/source-conversions`, {
    method: "POST",
    headers: { Authorization: AUTHORIZATION },
    body: form,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function getSourceConversion(jobId) {
  const response = await fetch(`${BASE_URL}/v1/source-conversions/${jobId}`, {
    headers: { Authorization: AUTHORIZATION },
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function getAnnotationResult(jobId) {
  const response = await fetch(`${BASE_URL}/v1/source-conversions/${jobId}/result`, {
    headers: { Authorization: AUTHORIZATION },
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function saveAnnotationResult(jobId, annotation) {
  const response = await fetch(`${BASE_URL}/v1/source-conversions/${jobId}/annotation`, {
    method: "PUT",
    headers: {
      Authorization: AUTHORIZATION,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(annotation),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
```
