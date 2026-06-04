# 当前问题记录

## 1. `run_examples_full_pipeline.sh` 全量运行失败

在 `oh/projects/md2json-api-project` 中执行 `run_examples_full_pipeline.sh` 后，当前结果显示整批失败：

- `example_full_pipeline_outputs/summary.json:2-6`

```json
{
  "run_id": "20260604T011354Z",
  "total": 40,
  "succeeded": 0,
  "failed": 40,
  "skipped": 0
}
```

### 直接表现
典型日志：

- `example_full_pipeline_outputs/logs/Numerical_Optimization_Chapter01.log:27-36`
- `example_full_pipeline_outputs/logs/Optimization_Theory_and_Methods_Chapter01.log:27-36`

报错为：

```text
RuntimeError: Doc2X request failed with HTTP 400: {"code":"bad_request","msg":"请求参数错误","detail":"body unmarshal failed"}
```

### 当前定位
失败发生在 Doc2X 导出 Markdown 阶段，而不是 md2json 阶段：

- `md2json_api/doc2x_client.py:86-94`

对应调用：

```python
export_request = {
    "uid": str(uid),
    "to": "md",
    "formula_mode": options["formula_mode"],
    "filename": source_file.stem,
    "merge_cross_page_forms": options["merge_cross_page_forms"],
    "formula_level": options["formula_level"],
}
self._post_json("/api/v2/convert/parse", export_request)
```

### 可能原因
目前最可能的原因有：

1. **`/api/v2/convert/parse` 的请求体格式不符合 Doc2X 当前服务要求**
   - 当前实现通过 `_post_json(...)` 发送 `application/json`
   - 如果服务端要求的是 `multipart/form-data` 或 `application/x-www-form-urlencoded`，则可能报：
     - `body unmarshal failed`

2. **字段类型与服务端预期不一致**
   - 例如 `merge_cross_page_forms` 当前传的是 Python `bool`
   - `formula_level` 当前是字符串
   - 如果服务端要求所有字段必须为字符串，或要求特定编码方式，也可能导致反序列化失败

3. **当前仓库中的 Doc2X 客户端实现与服务端接口版本不匹配**
   - 路径虽然是 `/api/v2/convert/parse`
   - 但接口的 body schema 可能已变化

---

## 2. 脚本中曾存在异常处理二次报错问题

此前 `run_examples_full_pipeline.sh` 的内嵌 Python 在异常处理分支中引用了未定义变量 `doc2x_progress`，导致原始错误被二次异常覆盖。

旧报错示例：

- `example_full_pipeline_outputs/logs/Numerical_Optimization_end.log:40-42`
- `example_full_pipeline_outputs/logs/Optimization_Theory_and_Methods_Chapter01.log:40-42`

```text
NameError: name 'doc2x_progress' is not defined. Did you mean: 'on_doc2x_progress'?
```

### 原因
异常处理中原先写的是：

```python
"doc2x_progress": doc2x_progress,
```

但实际维护的变量是：

```python
doc2x_progress_holder["value"]
```

### 当前状态
该问题已经在脚本中修复，新的代码应使用：

```python
"doc2x_progress": doc2x_progress_holder["value"],
```

但已有日志中仍可看到旧错误，这说明这些日志是在修复前生成的，或者运行进程在修复前已启动。

---

## 3. 当前问题总结

当前 `oh/projects/md2json-api-project` 里的主要问题是：

1. **主问题：Doc2X 导出接口 `POST /api/v2/convert/parse` 返回 400**
   - 报错：`body unmarshal failed`
   - 位置：`md2json_api/doc2x_client.py`
   - 影响：导致 full pipeline 在 Doc2X 导出阶段失败，后续 md2json 无法执行

2. **次问题：历史日志中存在旧版脚本的 `NameError` 痕迹**
   - 该问题已修复
   - 但旧产物仍会干扰判断

---

## 4. 建议的后续排查方向

### 优先级最高
检查并修正 `md2json_api/doc2x_client.py` 中 `/api/v2/convert/parse` 的请求格式：

- 当前：`application/json`
- 需要确认是否应改为：
  - `multipart/form-data`
  - 或 `application/x-www-form-urlencoded`
  - 或将字段统一编码为字符串

### 建议验证点
1. 打印或记录实际发送的 export payload（注意不要泄露敏感信息）
2. 对照 Doc2X 最新接口文档确认 body schema
3. 单独构造一个最小请求，验证：
   - JSON 是否被接受
   - `merge_cross_page_forms` 是否必须是字符串
   - `formula_level` 是否必须是整型或字符串

---

## 5. 说明

另一个仓库副本 `/root/workspace/wlm/md2json-api-project` 中已确认存在 `filename` 长度超过 50 的问题，并已修复。但那是 systemd 定时服务使用的代码副本，不是当前 `oh/projects/md2json-api-project` 这份示例运行仓库的根因。
