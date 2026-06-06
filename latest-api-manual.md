# 最新 Runner API 手册

本文档给前端和调用中间层一个单一口径，覆盖当前实际可用的：

- gateway 健康检查
- `statement` 服务
- `proof` 服务
- `resume / rerun_failed`
- `attempt_empty_proof`

> 更新时间：2026-05-11

---

## 1. 总览

Runner services 通过 gateway 暴露三组后端：

| Gateway path | 后端服务 | 用途 |
|---|---|---|
| `/statement/*` | `item_statement_runner_service` | statement / refine |
| `/proof/*` | `item_proof_runner_service` | proof |
| `/math/*` | `math_formalization_runner_service` | 更高层 formalization |

本手册只写当前最常用、最稳定的两组：

- `/statement/*`
- `/proof/*`

---

## 2. 基础配置

推荐环境变量：

```text
RUNNER_GATEWAY_BASE_URL=https://<gateway>/v1/8000
STATEMENT_RUNNER_BASE_URL=${RUNNER_GATEWAY_BASE_URL}/statement
PROOF_RUNNER_BASE_URL=${RUNNER_GATEWAY_BASE_URL}/proof
RUNNER_AUTHORIZATION=<token>
RUNNER_API_REPO=/volume/math/AI4M/users/zcwang/melon
```

请求头：

```http
Content-Type: application/json
Authorization: <RUNNER_AUTHORIZATION>
```

补充：

- `api_repo` 推荐传 `/volume/math/AI4M/users/zcwang/melon`
- 如果部署端已经设置了默认 `RUNNER_SERVICE_DEFAULT_API_REPO`，请求体可以省略 `api_repo`

---

## 3. 健康检查

```http
GET ${RUNNER_GATEWAY_BASE_URL}/
GET ${RUNNER_GATEWAY_BASE_URL}/readyz
GET ${STATEMENT_RUNNER_BASE_URL}/healthz
GET ${PROOF_RUNNER_BASE_URL}/healthz
```

推荐判断：

- gateway 返回路由信息：服务已启动
- `/readyz` 返回 `ok=true`：上游子服务可用
- `/statement/healthz`、`/proof/healthz` 返回 `ok=true`：单服务可接单

---

## 4. Statement 服务

### 4.1 创建 run

```http
POST ${STATEMENT_RUNNER_BASE_URL}/v1/item-statement-runs
```

最小请求体示例：

```json
{
  "data_json": "/abs/path/to/input.json",
  "project": "Serre",
  "lean_repo": "/abs/path/to/lean_repo",
  "api_repo": "/volume/math/AI4M/users/zcwang/melon",
  "statement_workers": 4,
  "refine_workers": 4,
  "fresh_run": true
}
```

常用字段：

- `data_json`
- `project`
- `lean_repo`
- `api_repo`
- `statement_workers`
- `refine_workers`
- `fresh_run`

说明：

- 这条 create 是 `statement -> refine` 的整条 service-managed run
- 如果只想继续某一轮失败 scope，优先走 `/resume`

### 4.2 查询

```http
GET ${STATEMENT_RUNNER_BASE_URL}/v1/item-statement-runs/{run_id}/progress
GET ${STATEMENT_RUNNER_BASE_URL}/v1/item-statement-runs/{run_id}/summary
GET ${STATEMENT_RUNNER_BASE_URL}/v1/item-statement-runs/{run_id}
GET ${STATEMENT_RUNNER_BASE_URL}/v1/item-statement-runs/{run_id}/logs?tail_lines=200
```

推荐优先看：

- `progress.status`
- `progress.runner_run_dir`
- `summary.phase_summary`

### 4.3 取消

```http
POST ${STATEMENT_RUNNER_BASE_URL}/v1/item-statement-runs/{run_id}/cancel
```

### 4.4 恢复

```http
POST ${STATEMENT_RUNNER_BASE_URL}/v1/item-statement-runs/{run_id}/resume
```

最小请求体：

```json
{
  "resume_mode": "rerun_failed"
}
```

当前实际语义：

- `resume` 会优先按 `.m2f_history/state.json` 重建 scope
- `statement` / `refine` 的恢复范围由 history 决定
- runner artifacts 只补充调试信息，不再决定最终 scope

---

## 5. Proof 服务

### 5.1 创建 run

```http
POST ${PROOF_RUNNER_BASE_URL}/v1/item-proof-runs
```

最小请求体示例：

```json
{
  "data_json": "/abs/path/to/input.json",
  "project": "Serre",
  "lean_repo": "/abs/path/to/lean_repo",
  "api_repo": "/volume/math/AI4M/users/zcwang/melon",
  "proof_workers": 12,
  "fresh_run": true
}
```

### 5.2 `attempt_empty_proof`

proof 服务现在支持：

```json
{
  "attempt_empty_proof": true
}
```

含义：

- 如果输入 item 的 `proof` 字段为空
- 不再直接跳过
- 会继续让模型根据：
  - statement
  - dependencies
  - 当前 Lean 文件上下文
  来尝试生成 proof

说明：

- 这不保证成功
- 它只表示“空 proof 也继续尝试跑”

推荐 proof create 示例：

```json
{
  "data_json": "/abs/path/to/input.json",
  "project": "Serre",
  "lean_repo": "/abs/path/to/lean_repo",
  "api_repo": "/volume/math/AI4M/users/zcwang/melon",
  "proof_workers": 12,
  "attempt_empty_proof": true,
  "fresh_run": true
}
```

### 5.3 查询

```http
GET ${PROOF_RUNNER_BASE_URL}/v1/item-proof-runs/{run_id}/progress
GET ${PROOF_RUNNER_BASE_URL}/v1/item-proof-runs/{run_id}/summary
GET ${PROOF_RUNNER_BASE_URL}/v1/item-proof-runs/{run_id}
GET ${PROOF_RUNNER_BASE_URL}/v1/item-proof-runs/{run_id}/logs?tail_lines=200
```

推荐优先看：

- `progress.status`
- `progress.runner_run_dir`
- `progress.resume_count`
- `progress.resume_decision`

### 5.4 取消

```http
POST ${PROOF_RUNNER_BASE_URL}/v1/item-proof-runs/{run_id}/cancel
```

当前 cancel 语义：

- 已经修到可以收口成 terminal
- 正常情况下会释放 repo lock
- 适合后续重新 create / resume

### 5.5 恢复

```http
POST ${PROOF_RUNNER_BASE_URL}/v1/item-proof-runs/{run_id}/resume
```

最小请求体：

```json
{
  "resume_mode": "rerun_failed"
}
```

如果要让恢复后的 proof 继续对空 proof 输入做尝试，可以加：

```json
{
  "resume_mode": "rerun_failed",
  "attempt_empty_proof": true
}
```

注意：

- proof resume 的 request schema **不接受** `api_repo` override
- `api_repo` 沿用原 run

---

## 6. History-first resume

当前恢复总原则：

- 正式唯一账本：
  - `<lean_repo>/.m2f_history/state.json`
- item 级状态固定只看顶层：
  - `item.statement`
  - `item.refine`
  - `item.proof`

不要再读旧口径：

- `item.stages.statement`
- `item.stages.refine`
- `item.stages.proof`

当前 `resume` 的实际语义：

- 继承的是 history 状态和 scope
- 不是恢复旧进程的内存现场

### Statement / Refine

- `statement resume` 看 `statement.status`
- `refine resume` 看 `statement.status + refine.status`

### Proof

- `proof resume` 看 `statement=success && refine=success && proof!=success`

---

## 7. 最重要的响应字段

前端最该看的是 `resume_decision`：

```json
{
  "resume_decision": {
    "requested_mode": "rerun_failed",
    "resolved_mode": "rerun_failed_scope",
    "scope_payload": {
      "scope_source": "repo_history",
      "selected_items_count": 106,
      "labels": ["...", "..."]
    }
  }
}
```

关键字段：

- `resolved_mode`
- `scope_payload.scope_source`
- `scope_payload.selected_items_count`
- `scope_payload.labels`

当前推荐期待：

- `scope_source = repo_history`

---

## 8. 常见运行态判断

### 正常推进

看这些信号：

- `progress.status = running`
- queue `updated_at` 持续刷新
- `running > 0`
- `success` 或 `failed` 在变化
- `stale_progress_workers = 0` 或较低

### 已正常收口

- `progress.status` 是终态：
  - `succeeded`
  - `failed`
  - `cancelled`
- queue:
  - `running = 0`
  - `waiting = 0`

### 假 running / stale

典型特征：

- service `progress.status = running`
- 但 queue / batch 很久不更新
- wrapper / batch pid 已死
- repo lock heartbeat 不刷新

这类需要：

1. 收口成 terminal
2. 清掉假 running
3. 再 create / resume

---

## 9. 推荐调用顺序

### 新起 proof

1. 从 `state.json` 派生 proof scope
2. `POST /proof/v1/item-proof-runs`
3. 轮询 `progress` 和 queue

### 跑失败后继续

1. 确认原 run 已终态
2. `POST /proof/v1/item-proof-runs/{run_id}/resume`
3. body:

```json
{
  "resume_mode": "rerun_failed",
  "attempt_empty_proof": true
}
```

### 观察结果

优先看：

- `summary.phase_summary`
- queue 的：
  - `jobs_total`
  - `success`
  - `failed`
  - `running`
  - `waiting`

---

## 10. 当前已知现实边界

1. proof 即使开了 `attempt_empty_proof=true`
   - 空 proof 输入也不保证成功
   - 只是会继续尝试

2. 大量 proof 失败通常集中在：
   - `blocked_by_dependency`
   - 空 proof 起跑后仍失败
   - `upstream_provider_error`
   - `no_meaningful_progress`

3. `repo_status.active_runs`
   - 现在已做 stale 清理
   - 但前端仍应以具体 run 的 `progress + queue` 为准

---

## 11. 相关文档

- [runner-services-integration.md](./runner-services-integration.md)
- [runner-resume-integration.md](./runner-resume-integration.md)
- [runner-fresh-run-history-notes.md](./runner-fresh-run-history-notes.md)
- [formalizer-integration.md](./formalizer-integration.md)
