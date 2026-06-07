# md2json API 手册

本文档面向前端和调用中间层，提供当前正式 API 对接口径，并补充 UI 功能与接口映射。文档结构尽量对齐 latest-api-manual 的组织方式：先说明接入方式与整体流程，再按接口分节说明请求、响应、错误与前端使用建议。

更新时间：2026-06-06

## 1. 文档目的与适用范围

标注系统前端当前通过统一入口 `/v1/source-conversions` 完成：

- 文件上传
- 任务轮询
- 转换结果读取
- 人工标注结果保存
- 已保存标注读取
- 质量摘要读取
- 用量统计读取

本文档只描述对前端公开的正式 HTTP API，不描述服务端内部 trace、Doc2X 内部调用、作业目录结构或第三方 key 配置。

## 2. 接入总览

当前推荐能力：

| 能力 | 入口 | 输出 |
|---|---|---|
| 多格式上传并转换为标注 JSON | `/v1/source-conversions` | `md2json.annotation.v1` |
| 查询原始文档解析任务列表 | `/v1/source-conversions` | 任务状态列表 |
| 下载实际送入 md2json 的 Markdown | `/v1/source-conversions/{job_id}/markdown` | `text/markdown` |
| 保存人工标注修改结果 | `/v1/source-conversions/{job_id}/annotation` | `md2json.annotation.v1` |
| 读取质量摘要 | `/v1/source-conversions/{job_id}/quality` | `quality` 对象 |
| 读取脱敏用量统计 | `/v1/source-conversions/{job_id}/usage` | usage 对象 |

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

## 3. 基础配置

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
- `MD2JSON_API_TOKEN` 是 md2json 服务自己的访问令牌。
- 它不是 OpenAI、Azure OpenAI 或 Doc2X 的第三方 API Key。
- 前端或前端中间层不需要传 OpenAI、Azure、Doc2X key。

推荐调用架构：

```text
浏览器 -> 前端/业务后端或网关 -> md2json
```

如果浏览器直接请求 md2json，需要前端团队自行确认 CORS、HTTPS/mixed content 和 token 暴露风险。生产环境更推荐通过前端业务后端或网关代理 md2json，请勿将服务 token 直接暴露给不受控浏览器环境；浏览器跨域、代理和域名网关由前端团队按其部署环境处理。

## 4. UI 功能与 API 对应关系

这一节用于把 UI 设计中的主要功能区与接口直接对齐，便于前端联调和页面说明书引用。

### 4.1 上传页 / 新建任务

| UI 功能 | 接口 | 方法 | 说明 |
|---|---|---|---|
| 选择文件并点击“开始转换” | `/v1/source-conversions` | `POST` | 创建异步转换任务，返回 `job_id` |
| 展示文件类型支持说明 | 无单独接口 | - | 由前端按本手册静态展示 |
| 展示当前策略由服务端统一控制 | 无单独接口 | - | 前端无需提交任何转换策略或 Doc2X 参数 |

前端动作建议：

1. 用户选中文件。
2. 前端调用 `POST /v1/source-conversions`。
3. 以返回的 `job_id` 进入任务详情页或处理中状态页。

### 4.2 处理中页面 / 任务状态页

| UI 功能 | 接口 | 方法 | 说明 |
|---|---|---|---|
| 展示“排队中/处理中/成功/失败” | `/v1/source-conversions/{job_id}` | `GET` | 读取 `status`、`phase` |
| 展示 OCR 或预处理进度 | `/v1/source-conversions/{job_id}` | `GET` | 读取 `preprocess_progress` |
| 展示 section 处理进度 | `/v1/source-conversions/{job_id}` | `GET` | 读取 `sections_total`、`sections_completed` |
| 失败时显示错误提示 | `/v1/source-conversions/{job_id}` | `GET` | 读取 `error` |

前端动作建议：

- Markdown 任务每 2-5 秒轮询一次。
- PDF / 图片任务每 5-10 秒轮询一次。
- 状态变为 `succeeded` 后，进入结果页并调用 `/result`。
- 状态变为 `failed` 后，停止轮询并展示 `job_id` 供排查。

### 4.3 标注结果页 / 结构化内容编辑页

| UI 功能 | 接口 | 方法 | 说明 |
|---|---|---|---|
| 首次加载自动抽取结果 | `/v1/source-conversions/{job_id}/result` | `GET` | 获取当前可编辑 annotation 文档 |
| 回显用户之前保存过的版本 | `/v1/source-conversions/{job_id}/result` | `GET` | 如果该任务已保存过 annotation，返回保存版本 |
| 读取仅人工保存版本 | `/v1/source-conversions/{job_id}/annotation` | `GET` | 只在明确需要“已保存版本”时使用 |
| 展示 item 列表、正文、proof、依赖 | `/v1/source-conversions/{job_id}/result` | `GET` | 主要读取 `items[]` |

说明：

- 普通结果页建议优先用 `/result`，因为它天然兼容“未保存时返回自动结果、已保存时返回保存版本”的场景。
- 如果 UI 上存在“查看我上次保存版本”的独立功能，再单独调用 `/annotation`。

### 4.4 标注编辑保存

| UI 功能 | 接口 | 方法 | 说明 |
|---|---|---|---|
| 点击“保存标注” | `/v1/source-conversions/{job_id}/annotation` | `PUT` | 提交完整 `md2json.annotation.v1` 文档 |
| 保存成功提示 | `/v1/source-conversions/{job_id}/annotation` | `PUT` | 根据返回的 `saved=true`、`item_count` 展示 |

前端注意：

- 当前保存接口要求提交完整 annotation 文档，不是 patch。
- 前端应以最近一次从 `/result` 或 `/annotation` 获取的完整文档为基础编辑并整体提交。

### 4.5 质量检查面板 / 问题列表抽屉

| UI 功能 | 接口 | 方法 | 说明 |
|---|---|---|---|
| 展示全局错误数、警告数 | `/v1/source-conversions/{job_id}/quality` | `GET` | 读取 `error_count`、`warning_count` |
| 展示问题列表 | `/v1/source-conversions/{job_id}/quality` | `GET` | 读取 `issues[]` |
| 从问题跳转到对应 item | `/v1/source-conversions/{job_id}/quality` | `GET` | 读取 `issues[].item_id` |
| 在 item 局部展示问题 | `/v1/source-conversions/{job_id}/result` | `GET` | 读取 `items[].audit.issues` |

说明：

- 全局问题面板建议使用 `/quality`。
- 单个条目右侧或行内警告，建议直接使用 `/result` 返回的 `items[].audit.issues`。

### 4.6 任务信息 / 成本信息弹窗

| UI 功能 | 接口 | 方法 | 说明 |
|---|---|---|---|
| 展示请求次数、token、耗时 | `/v1/source-conversions/{job_id}/usage` | `GET` | 用于脱敏展示统计信息 |

说明：

- `usage` 字段后续可能扩展，前端不要强绑定全部字段。
- 该接口更适合“详情弹窗/调试信息区”，不建议作为主流程强依赖。

### 4.7 健康检查 / 运维探活

| UI 功能 | 接口 | 方法 | 说明 |
|---|---|---|---|
| 环境探活、联调自检 | `/healthz` | `GET` | 返回服务存活状态 |

## 5. 前端标准调用流程

推荐的 UI 调用顺序：

```text
上传文件
  -> POST /v1/source-conversions
  -> 拿到 job_id
  -> GET /v1/source-conversions/{job_id} 轮询状态
  -> 成功后 GET /v1/source-conversions/{job_id}/result
  -> 用户编辑
  -> PUT /v1/source-conversions/{job_id}/annotation
  -> 如需质量摘要，GET /v1/source-conversions/{job_id}/quality
  -> 如需用量统计，GET /v1/source-conversions/{job_id}/usage
```

如果页面需要“恢复上次人工保存结果”，可以额外调用：

```text
GET /v1/source-conversions/{job_id}/annotation
```

## 6. 健康检查

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

## 7. 统一转换服务

### 7.1 创建任务

```http
POST ${MD2JSON_BASE_URL}/v1/source-conversions
Content-Type: multipart/form-data
Authorization: Bearer <MD2JSON_API_TOKEN>
```

对应 UI：

- 上传页“开始转换”按钮
- 新建任务弹窗“确认上传”按钮

请求参数：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---:|---:|---|---|
| `file` | file | 是 | 无 | `.md`、`.pdf`、`.jpg`、`.jpeg`、`.png` |

转换策略、模型、reasoning、Doc2X 参数均由服务端统一控制，前端不提交这些字段。

示例：

```bash
curl -X POST "$MD2JSON_BASE_URL/v1/source-conversions" \
  -H "Authorization: Bearer <MD2JSON_API_TOKEN>" \
  -F "file=@chapter.md;type=text/markdown"
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

### 7.2 查询任务

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}
Authorization: Bearer <MD2JSON_API_TOKEN>
```

对应 UI：

- 处理中页面
- 任务详情页
- 上传后自动轮询逻辑

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

| status | 含义 | UI 建议 |
|---|---|---|
| `queued` | 已入队 | 显示“排队中” |
| `running` | 正在处理 | 显示进度和阶段 |
| `succeeded` | 成功，可以获取结果 | 自动进入结果页 |
| `failed` | 失败 | 停止轮询，展示错误和 `job_id` |

建议轮询间隔：

- Markdown：2-5 秒。
- PDF / 图片：5-10 秒。
- 5xx 或网络异常时做退避重试。

### 7.3 获取结果

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/result
Authorization: Bearer <MD2JSON_API_TOKEN>
```

对应 UI：

- 标注结果页首次加载
- 编辑页重新打开
- 结果详情页

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

| 字段 | 用途 | 典型 UI |
|---|---|---|
| `schema_version` | 判断兼容版本 | 页面初始化校验 |
| `source.filename` | 展示源文件名 | 顶部标题区 |
| `source.source_type` | 区分 Markdown、PDF、图片 | 文件类型标签 |
| `items[].id` | block 稳定主键 | 列表 key、定位 |
| `items[].order_index` | block 展示顺序 | 左侧条目列表 |
| `items[].type` | block 类型 | 类型 tag |
| `items[].label` | 条目编号或名称 | 列表标题 |
| `items[].statement` | 可编辑正文 | 主编辑区 |
| `items[].proof` | 可编辑证明 | proof 编辑区 |
| `items[].dependencies` | 依赖/引用列表 | 依赖面板 |
| `items[].source_refs.pages` | 点击 block 后跳转原文页 | 原文页码跳转 |
| `items[].source_refs.bbox_refs` | 后续原文区域高亮 | 原文高亮联动 |
| `items[].assets` | 图片、表格、caption | 图表展示区 |
| `items[].audit.issues` | item 级错误/警告 | 条目侧边提示 |
| `quality.error_count` | 全局错误数 | 页面顶部摘要 |
| `quality.warning_count` | 全局警告数 | 页面顶部摘要 |
| `quality.issues[]` | 可跳转问题列表 | 质量抽屉/问题面板 |

`type` 常见值：

```text
def, thm, prop, lemma, cor, remark, example, exercise, algorithm,
assumption, claim, conjecture, problem, question, notation,
heading, paragraph, figure, table, unknown
```

### 7.4 下载 markdown

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/markdown
Authorization: Bearer <MD2JSON_API_TOKEN>
```

对应 UI：

- 历史记录页“下载 Markdown”按钮
- 结果详情页 markdown 下载入口

只有 `status=succeeded` 后才能调用。任务未完成时返回 `409`。

成功响应：

- `200 OK`
- `Content-Type: text/markdown; charset=utf-8`

返回内容语义：

- 对 markdown 上传：返回原始上传并实际送入 md2json 的 markdown 文本
- 对 PDF / 图片上传：返回 Doc2X / OCR 生成并实际送入 md2json 的 markdown 文本

该接口返回的是**实际用于 md2json 抽取的 markdown**，不是 trace、内部切片或调试信息。

### 7.5 保存标注结果

前端完成人工编辑后，提交完整的 `md2json.annotation.v1` 文档。

```http
PUT ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/annotation
Content-Type: application/json
Authorization: Bearer <MD2JSON_API_TOKEN>
```

对应 UI：

- 编辑页“保存”按钮
- 自动保存触发器（如果前端后续要做）

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

### 7.6 获取已保存标注结果

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/annotation
Authorization: Bearer <MD2JSON_API_TOKEN>
```

对应 UI：

- “查看已保存版本”
- “恢复上次保存结果”

返回最近一次保存的完整 `md2json.annotation.v1` 文档。如果尚未保存过人工标注结果，返回 `404`。

### 7.7 获取质量摘要

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/quality
Authorization: Bearer <MD2JSON_API_TOKEN>
```

对应 UI：

- 质量检查抽屉
- 顶部错误/警告计数卡片
- 问题列表侧边栏

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

### 7.8 获取用量统计

```http
GET ${MD2JSON_BASE_URL}/v1/source-conversions/{job_id}/usage
Authorization: Bearer <MD2JSON_API_TOKEN>
```

对应 UI：

- 任务详情弹窗
- 调试信息面板
- 成本/耗时信息区

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

## 8. 错误码

错误响应通常为：

```json
{
  "detail": "错误说明"
}
```

常见状态码：

| 状态码 | 场景 | 前端处理建议 |
|---:|---|---|
| `400` | 文件类型不支持、参数值不支持、空文件 | 提示用户修改输入 |
| `401` | 缺少或错误的 `Authorization` | 提示登录态/网关配置问题 |
| `404` | `job_id` 不存在，或 annotation 尚未保存 | 提示资源不存在 |
| `409` | 任务未完成时获取结果 | 返回处理中页继续轮询 |
| `413` | 上传文件超过服务端限制 | 提示用户更换更小文件 |
| `500` | 服务端异常 | 提示稍后重试，并保留 `job_id` |

## 9. JavaScript 示例

```js
const BASE_URL = "http://8.211.159.42/md2json";
const AUTHORIZATION = "Bearer <MD2JSON_API_TOKEN>";

export async function createSourceConversion(file) {
  const form = new FormData();
  form.append("file", file);

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

export async function getSavedAnnotation(jobId) {
  const response = await fetch(`${BASE_URL}/v1/source-conversions/${jobId}/annotation`, {
    headers: { Authorization: AUTHORIZATION },
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function getQuality(jobId) {
  const response = await fetch(`${BASE_URL}/v1/source-conversions/${jobId}/quality`, {
    headers: { Authorization: AUTHORIZATION },
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function getUsage(jobId) {
  const response = await fetch(`${BASE_URL}/v1/source-conversions/${jobId}/usage`, {
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

## 10. 前端落地建议

- 主流程只强依赖这四个接口：
  - `POST /v1/source-conversions`
  - `GET /v1/source-conversions/{job_id}`
  - `GET /v1/source-conversions/{job_id}/result`
  - `PUT /v1/source-conversions/{job_id}/annotation`
- `GET /annotation` 适合“恢复保存版本”功能，不必作为默认首屏必调接口。
- `GET /quality` 和 `GET /usage` 更适合作为增强能力，不建议阻塞主编辑流程。
- 前端数据模型优先对齐 `md2json.annotation.v1`，避免自行拆成多个不兼容对象。
- 如果后续 UI 设计稿里出现新的页面模块，可以直接按“UI 功能 -> 接口 -> 字段”方式继续在第 4 节补充。