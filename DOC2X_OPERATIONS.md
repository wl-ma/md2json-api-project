# Doc2X 运维自动化操作流程

本文说明两个生产运维任务：

1. 自动清理 md2json / Doc2X 作业缓存。
2. 自动扫描服务器本地 PDF 目录，调用 Doc2X 前置阶段，并把 Markdown / JSON 结果保存到指定目录。

所有真实 key 只应写入服务器本地 `/etc/md2json/md2json.env` 或等价的受限配置文件，不要提交到仓库，不要粘贴到日志或聊天中。

## 1. 环境变量配置

编辑服务器环境文件：

```bash
sudoedit /etc/md2json/md2json.env
```

保留现有 `MD2JSON_*`、`OPENAI_*` 或 `AZURE_*` 配置，并追加：

```text
DOC2X_API_KEY=your_doc2x_api_key_here
DOC2X_BASE_URL=https://v2.doc2x.noedgeai.com
DOC2X_MODEL=v3-2026
DOC2X_TIMEOUT=600
DOC2X_POLL_INTERVAL=2
DOC2X_MAX_UPLOAD_BYTES=314572800
DOC2X_WORKERS=1
FULL_CONVERSION_WORKERS=1

MD2JSON_RETENTION_DAYS=7

DOC2X_WATCH_INPUT_DIR=/data/pdf-inbox
DOC2X_WATCH_OUTPUT_DIR=/data/doc2x-output
DOC2X_WATCH_STABLE_SECONDS=30
DOC2X_WATCH_MAX_FILES=0
DOC2X_WATCH_RECURSIVE=false
DOC2X_FORMULA_MODE=normal
DOC2X_FORMULA_LEVEL=0
DOC2X_MERGE_CROSS_PAGE_FORMS=false
```

注意：

- `DOC2X_API_KEY` 必填。
- `DOC2X_WATCH_INPUT_DIR` 是待识别 PDF 目录。
- `DOC2X_WATCH_OUTPUT_DIR` 是识别结果输出目录。
- `DOC2X_WATCH_STABLE_SECONDS` 用于避免处理仍在上传中的文件。
- `DOC2X_WATCH_MAX_FILES=0` 表示每次扫描不限制处理数量；生产初期可设为 `1` 或 `5` 控制调用节奏。
- `EnvironmentFile` 中不要在值后面添加内联注释或表格字符，例如不要写 `DOC2X_WORKERS=1 # comment`。

确认权限：

```bash
sudo chmod 600 /etc/md2json/md2json.env
```

创建目录并设置权限，按实际服务用户替换 `md2json`：

```bash
sudo install -d -m 0750 -o md2json -g md2json /data/pdf-inbox
sudo install -d -m 0750 -o md2json -g md2json /data/doc2x-output
```

## 2. 部署代码

假设项目部署目录是：

```text
/root/workspace/wlm/md2json-api-project
```

更新代码后检查脚本：

```bash
cd /root/workspace/wlm/md2json-api-project
.venv/bin/python -m py_compile scripts/cleanup_jobs.py scripts/doc2x_watch_folder.py
.venv/bin/python -m compileall md2json_api scripts
```

脚本可直接从仓库路径运行，不需要复制到 `/usr/local/bin`。

## 3. 自动清理作业缓存

创建 systemd service：

```bash
sudoedit /etc/systemd/system/md2json-cleanup.service
```

写入：

```ini
[Unit]
Description=Clean old md2json job artifacts

[Service]
Type=oneshot
EnvironmentFile=/etc/md2json/md2json.env
WorkingDirectory=/root/workspace/wlm/md2json-api-project
ExecStart=/root/workspace/wlm/md2json-api-project/.venv/bin/python scripts/cleanup_jobs.py
```

创建 timer：

```bash
sudoedit /etc/systemd/system/md2json-cleanup.timer
```

写入：

```ini
[Unit]
Description=Run md2json job cleanup daily

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now md2json-cleanup.timer
```

先 dry-run 检查：

```bash
cd /root/workspace/wlm/md2json-api-project
sudo systemctl stop md2json-cleanup.timer
sudo -E /root/workspace/wlm/md2json-api-project/.venv/bin/python scripts/cleanup_jobs.py --dry-run
sudo systemctl start md2json-cleanup.timer
```

手动运行一次：

```bash
sudo systemctl start md2json-cleanup.service
sudo journalctl -u md2json-cleanup.service -n 80 --no-pager
```

查看定时器：

```bash
systemctl list-timers 'md2json-cleanup*'
```

清理策略：

- 删除 `${MD2JSON_JOBS_ROOT}/doc2x/*` 中超过保留期的目录。
- 删除 `${MD2JSON_JOBS_ROOT}/full/*` 中超过保留期的目录。
- 删除 `${MD2JSON_JOBS_ROOT}/{32位hex job_id}` 中超过保留期的 Markdown-only 目录。
- 不删除 `jobs.sqlite3`、`doc2x_jobs.sqlite3`、`full_jobs.sqlite3`。

## 4. 自动扫描 PDF 并执行 Doc2X

扫描脚本对 PDF 扩展名大小写不敏感，`.pdf`、`.PDF`、`.Pdf` 都会被识别。

创建 systemd service：

```bash
sudoedit /etc/systemd/system/md2json-doc2x-folder.service
```

写入：

```ini
[Unit]
Description=Run Doc2X conversion for PDFs in watched folder
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/md2json/md2json.env
WorkingDirectory=/root/workspace/wlm/md2json-api-project
ExecStart=/root/workspace/wlm/md2json-api-project/.venv/bin/python scripts/doc2x_watch_folder.py
```

创建 timer：

```bash
sudoedit /etc/systemd/system/md2json-doc2x-folder.timer
```

写入：

```ini
[Unit]
Description=Scan PDF folder for Doc2X conversion every minute

[Timer]
OnBootSec=1min
OnUnitInactiveSec=1min
Persistent=true

[Install]
WantedBy=timers.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now md2json-doc2x-folder.timer
```

手动运行一次：

```bash
sudo systemctl start md2json-doc2x-folder.service
sudo journalctl -u md2json-doc2x-folder.service -n 120 --no-pager
```

查看定时器：

```bash
systemctl list-timers 'md2json-doc2x-folder*'
```

## 5. 输出目录结构

假设输入文件为：

```text
/data/pdf-inbox/book.pdf
```

输出为：

```text
/data/doc2x-output/book/
  output.md
  pages.json
  export.zip
  export_manifest.json
  source.sha256
  done.json
```

如果设置了 `DOC2X_WATCH_RECURSIVE=true`，输入为：

```text
/data/pdf-inbox/subdir/book.pdf
```

输出为：

```text
/data/doc2x-output/subdir/book/
  output.md
  pages.json
  export.zip
  export_manifest.json
  source.sha256
  done.json
```

跳过重复处理的规则：

- 脚本计算 PDF 的 SHA-256。
- 如果输出目录中存在 `done.json`，且 `source_sha256` 等于当前 PDF 的 SHA-256，就跳过。
- 如果同名 PDF 内容变化，SHA-256 不同，会重新调用 Doc2X 并覆盖该输出目录的结果。

失败时会写：

```text
failed.json
```

下次 timer 运行时会再次尝试，除非已有匹配的 `done.json`。

## 6. 本地命令测试

不经过 systemd，直接跑一次扫描：

```bash
cd /root/workspace/wlm/md2json-api-project

set -a
source /etc/md2json/md2json.env
set +a

.venv/bin/python scripts/doc2x_watch_folder.py \
  --input-dir /data/pdf-inbox \
  --output-dir /data/doc2x-output \
  --max-files 1
```

注意：不要执行会打印全部环境变量的命令。

查看结果：

```bash
find /data/doc2x-output -maxdepth 3 -type f \
  \( -name 'output.md' -o -name 'pages.json' -o -name 'done.json' -o -name 'failed.json' \) \
  -print
```

查看某个 Markdown 结果：

```bash
sed -n '1,80p' /data/doc2x-output/book/output.md
```

检查 JSON 格式：

```bash
python3 -m json.tool /data/doc2x-output/book/pages.json >/tmp/pages.pretty.json
```

## 7. 和 HTTP 服务的关系

这个目录扫描任务不走 HTTP API，而是直接调用项目内 `Doc2XClient`。理由：

- PDF 已经在同一台服务器上，不需要再通过 HTTP 上传给本机服务。
- 输出目录和跳过规则由 `done.json` 明确控制。
- 不需要额外管理 `MD2JSON_API_TOKEN`。

HTTP 服务仍然可以同时提供：

- `/v1/doc2x-conversions`：客户端上传 PDF 获取 Doc2X Markdown / JSON。
- `/v1/full-conversions`：客户端上传 PDF 获取最终 item JSON。
- `/v1/conversions`：客户端上传 Markdown 获取最终 item JSON。

两套机制共用 `DOC2X_API_KEY`，但作业目录不同：HTTP 服务写入 `${MD2JSON_JOBS_ROOT}`，目录扫描任务写入 `DOC2X_WATCH_OUTPUT_DIR`。
