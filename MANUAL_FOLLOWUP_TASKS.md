
## 重启 md2json-mdonly-folder 服务与定时器

```bash
sudo systemctl daemon-reload
sudo systemctl restart md2json-mdonly-folder.service
sudo systemctl restart md2json-mdonly-folder.timer
sudo systemctl status md2json-mdonly-folder.service --no-pager
sudo systemctl status md2json-mdonly-folder.timer --no-pager
```
# md2json-api-project 手动后续操作说明

本文档记录当前检查结论，以及你接下来需要手动完成的操作。

---

## 一、当前检查结论

### 1. `MD2JSON_JOBS_ROOT` 当前 systemd 服务实际配置
`md2json-api.service` 使用：

- `EnvironmentFile=/etc/md2json/md2json.env`

环境文件中当前值为：

```text
MD2JSON_JOBS_ROOT=/srv/md2json/jobs
```

因此，**当前正在运行的 `md2json-api.service` 实际使用的任务缓存根目录是：**

```text
/srv/md2json/jobs
```

### 2. 为什么本地 dry-run 显示的是另一个目录
在未显式 `source /etc/md2json/md2json.env` 的情况下，直接在项目目录执行：

```bash
./.venv/bin/python - <<'PY'
from md2json_api.jobs import WorkerSettings
s = WorkerSettings.from_environment()
print(s.jobs_root)
PY
```

得到的是：

```text
/root/workspace/wlm/md2json-api-project/var/jobs
```

这是因为：
- `WorkerSettings.from_environment()` 在当前 shell 中没有读到 `MD2JSON_JOBS_ROOT`
- 因此回退到了代码默认路径

所以，**前面 dry-run 实际检查的是默认目录，不是线上服务当前使用的 `/srv/md2json/jobs`**。

---

### 3. `md2json-cleanup.service` 其实已经部署了
当前系统中已经存在：

- `/etc/systemd/system/md2json-cleanup.service`
- `/etc/systemd/system/md2json-cleanup.timer`

但它现在执行的是旧脚本：

```text
ExecStart=/root/workspace/wlm/md2json-api-project/.venv/bin/python scripts/cleanup_jobs.py
```

而不是这次新实现的：

- `scripts/cleanup_cache.py`
- 或 `python -m md2json_api.cli cleanup-cache`

因此结论是：

### 结论
- **不是“还没有部署 md2json-cleanup.service”**
- 而是：
  - **已经部署了旧版 cleanup 服务**
  - **现在需要把它更新为新版 cleanup 机制**

---

## 二、建议的手动操作顺序

建议按以下顺序执行：

1. 先确认 `/srv/md2json/jobs` 当前内容
2. 对真实 jobs root 运行一次 dry-run
3. 更新 cleanup service 到新版脚本
4. 更新环境变量
5. 部署新的 md-only watcher（扫描 `ebooks-doc2x` 并输出到 `ebooks-md2json`）
6. 重启 `md2json-api.service`
7. 重启 cleanup timer / 手工跑一次 cleanup service
8. 启用并验证 md-only watcher timer
9. 查看日志确认运行正常

---

## 三、详细操作指令

### 步骤 1：检查真实 jobs root 当前内容

```bash
sudo ls -lah /srv/md2json/jobs
sudo find /srv/md2json/jobs -maxdepth 3 -type d | head -200
```

如果目录不存在，也请确认：

```bash
sudo mkdir -p /srv/md2json/jobs
sudo chown -R root:root /srv/md2json/jobs
```

> 如果服务实际不是以 root 运行，而是专门的服务用户，请把属主改成对应用户。

---

### 步骤 2：针对真实 jobs root 做 dry-run

必须先加载 systemd 使用的环境文件：

```bash
cd /root/workspace/wlm/md2json-api-project
set -a
source /etc/md2json/md2json.env
set +a
./.venv/bin/python scripts/cleanup_cache.py --dry-run
```

或者显式指定：

```bash
cd /root/workspace/wlm/md2json-api-project
./.venv/bin/python scripts/cleanup_cache.py --jobs-root /srv/md2json/jobs --dry-run
```

如果想走 CLI：

```bash
cd /root/workspace/wlm/md2json-api-project
set -a
source /etc/md2json/md2json.env
set +a
./.venv/bin/python -m md2json_api.cli cleanup-cache --dry-run
```

---

### 步骤 3：更新 `/etc/md2json/md2json.env`

编辑：

```bash
sudo editor /etc/md2json/md2json.env
```

在文件中追加或更新为：

```text
MD2JSON_CACHE_GC_ENABLED=true
MD2JSON_CACHE_DELETE_FAILED_AFTER_DAYS=7
MD2JSON_CACHE_DELETE_SUCCEEDED_AFTER_DAYS=14
MD2JSON_CACHE_DELETE_ANNOTATED_AFTER_DAYS=180
MD2JSON_CACHE_DELETE_ANNOTATION_DOCUMENTS_AFTER_DAYS=180
MD2JSON_CACHE_KEEP_ANNOTATION_DOCUMENTS=true
MD2JSON_CACHE_DELETE_DEBUG_AFTER_DAYS=1
MD2JSON_CACHE_GC_BATCH_LIMIT=500

MD2JSON_DOC2X_INPUT_ROOT=/root/workspace/data/book_prepare/ebooks-doc2x
MD2JSON_MDONLY_WORK_ROOT=/root/workspace/data/book_prepare/ebooks-md2json-work
MD2JSON_MDONLY_OUTPUT_ROOT=/root/workspace/data/book_prepare/ebooks-md2json
MD2JSON_MDONLY_STABLE_SECONDS=30
MD2JSON_MDONLY_MAX_FILES=1
MD2JSON_MDONLY_MAX_FAILURES=3
MD2JSON_MDONLY_RETRY_COOLDOWN_SECONDS=3600
```

> 如果你想先保守观察而不真正删除，可以临时再加：
>
> ```text
> MD2JSON_CACHE_GC_DRY_RUN=true
> ```
>
> 等确认没问题后再删掉或改成 `false`。

---

### 步骤 4：把 cleanup service 改成新版脚本

当前 `md2json-cleanup.service` 还在调用旧脚本：

```text
scripts/cleanup_jobs.py
```

建议改成：

```text
scripts/cleanup_cache.py
```

执行：

```bash
sudo tee /etc/systemd/system/md2json-cleanup.service >/dev/null <<'EOF'
[Unit]
Description=Clean old md2json job artifacts

[Service]
Type=oneshot
EnvironmentFile=/etc/md2json/md2json.env
WorkingDirectory=/root/workspace/wlm/md2json-api-project
ExecStart=/root/workspace/wlm/md2json-api-project/.venv/bin/python scripts/cleanup_cache.py
EOF
```

如果你更希望走 CLI，也可以改成：

```text
ExecStart=/root/workspace/wlm/md2json-api-project/.venv/bin/python -m md2json_api.cli cleanup-cache
```

---

### 步骤 5：重新加载 systemd 配置

```bash
sudo systemctl daemon-reload
```

---

### 步骤 5：部署新的 md-only watcher（扫描 `ebooks-doc2x`）

先创建输出目录：

```bash
sudo mkdir -p /root/workspace/data/book_prepare/ebooks-md2json-work
sudo mkdir -p /root/workspace/data/book_prepare/ebooks-md2json
```

如需调整属主，请按服务实际运行用户修改：

```bash
sudo chown -R root:root /root/workspace/data/book_prepare/ebooks-md2json-work
sudo chown -R root:root /root/workspace/data/book_prepare/ebooks-md2json
```

先手工 dry-run 一次脚本逻辑：

```bash
cd /root/workspace/wlm/md2json-api-project
set -a
source /etc/md2json/md2json.env
set +a
./.venv/bin/python scripts/md2json_watch_doc2x_folder.py --dry-run --max-files 1
```

确认 dry-run 输出没有问题后，再执行真实处理：

```bash
cd /root/workspace/wlm/md2json-api-project
set -a
source /etc/md2json/md2json.env
set +a
./.venv/bin/python scripts/md2json_watch_doc2x_folder.py --dry-run --max-files 1
```

```bash
cd /root/workspace/wlm/md2json-api-project
set -a
source /etc/md2json/md2json.env
set +a
./.venv/bin/python scripts/md2json_watch_doc2x_folder.py --max-files 1
```

如果手工运行正常，再部署 systemd：

```bash
sudo tee /etc/systemd/system/md2json-mdonly-folder.service >/dev/null <<'EOF'
[Unit]
Description=Run md2json on completed Doc2X folder results
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/md2json/md2json.env
WorkingDirectory=/root/workspace/wlm/md2json-api-project
ExecStart=/root/workspace/wlm/md2json-api-project/.venv/bin/python scripts/md2json_watch_doc2x_folder.py
EOF
```

```bash
sudo tee /etc/systemd/system/md2json-mdonly-folder.timer >/dev/null <<'EOF'
[Unit]
Description=Scan completed Doc2X folder results and run md2json

[Timer]
OnBootSec=10min
OnUnitInactiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF
```

重新加载并启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now md2json-mdonly-folder.timer
sudo systemctl status md2json-mdonly-folder.timer --no-pager
```

手工触发一轮：

```bash
sudo systemctl start md2json-mdonly-folder.service
sudo systemctl status md2json-mdonly-folder.service --no-pager
journalctl -u md2json-mdonly-folder.service -n 100 --no-pager
```

---

### 步骤 6：重启 md2json API 服务，让新代码生效

当前 `md2json-api.service` 正在运行旧进程，需要重启。

执行：

```bash
sudo systemctl restart md2json-api.service
sudo systemctl status md2json-api.service --no-pager
```

建议进一步看日志：

```bash
journalctl -u md2json-api.service -n 100 --no-pager
```

---

### 步骤 7：重启 cleanup timer，并手工执行一次 cleanup service

先确保 timer 重新加载：

```bash
sudo systemctl restart md2json-cleanup.timer
sudo systemctl status md2json-cleanup.timer --no-pager
```

手工跑一轮：

```bash
sudo systemctl start md2json-cleanup.service
sudo systemctl status md2json-cleanup.service --no-pager
```

查看日志：

```bash
journalctl -u md2json-cleanup.service -n 100 --no-pager
```

---

### 步骤 8：验证 md-only watcher 输出

检查是否已生成：

```bash
find /root/workspace/data/book_prepare/ebooks-md2json -maxdepth 4 -type f | sort | head -200
find /root/workspace/data/book_prepare/ebooks-md2json-work -maxdepth 4 -type d | sort | head -200
```

重点查看单本书目录下是否生成：

```text
done.json
failed.json / blocked.json（如失败）
meta.json
result.json
quality.json
usage.json
```

---

## 四、建议的上线前检查

### 1. 确认 API 服务仍然可用

```bash
curl -s http://127.0.0.1:8125/healthz
```

如果服务有鉴权以外的代理层，也可用实际地址测：

```bash
curl -s http://8.211.159.42/md2json/healthz
```

---

### 2. 确认新接口是否生效

例如（需要 token）：

```bash
curl -s \
  -H "Authorization: Bearer $(grep '^MD2JSON_API_TOKEN=' /etc/md2json/md2json.env | cut -d= -f2-)" \
  "http://127.0.0.1:8125/v1/source-conversions?limit=5"
```

以及：

```bash
curl -s \
  -H "Authorization: Bearer $(grep '^MD2JSON_API_TOKEN=' /etc/md2json/md2json.env | cut -d= -f2-)" \
  "http://127.0.0.1:8125/v1/annotation-documents?limit=5"
```

---

### 3. 再做一次真实 jobs root 的 dry-run

```bash
cd /root/workspace/wlm/md2json-api-project
set -a
source /etc/md2json/md2json.env
set +a
./.venv/bin/python scripts/cleanup_cache.py --dry-run
```

---

## 五、如果想先保守上线

如果你担心自动删除太激进，建议采用这个顺序：

1. 先在 `/etc/md2json/md2json.env` 中设置：

```text
MD2JSON_CACHE_GC_DRY_RUN=true
```

2. 重启 cleanup timer / service
3. 观察 1~2 天日志
4. 确认没有误删风险后，再删掉这一行或改为：

```text
MD2JSON_CACHE_GC_DRY_RUN=false
```

然后再：

```bash
sudo systemctl restart md2json-cleanup.service
```

---

## 六、当前状态总结

### 已确认
- `md2json-api.service` 正在运行
- 它实际使用的 `MD2JSON_JOBS_ROOT` 是：

```text
/srv/md2json/jobs
```

- `md2json-cleanup.service` **已经部署**，但现在还是旧版脚本
- 需要更新成新版 `cleanup_cache.py`
- 为了使新 API 代码和缓存清理机制真正生效，需要重启 `md2json-api.service`

---

## 七、最短执行清单

如果只想按最短路径操作，执行下面这一组：

```bash
cd /root/workspace/wlm/md2json-api-project
set -a
source /etc/md2json/md2json.env
set +a
./.venv/bin/python scripts/cleanup_cache.py --jobs-root /srv/md2json/jobs --dry-run
```

```bash
sudo editor /etc/md2json/md2json.env
```

加入：

```text
MD2JSON_CACHE_GC_ENABLED=true
MD2JSON_CACHE_DELETE_FAILED_AFTER_DAYS=7
MD2JSON_CACHE_DELETE_SUCCEEDED_AFTER_DAYS=14
MD2JSON_CACHE_DELETE_ANNOTATED_AFTER_DAYS=180
MD2JSON_CACHE_DELETE_ANNOTATION_DOCUMENTS_AFTER_DAYS=180
MD2JSON_CACHE_KEEP_ANNOTATION_DOCUMENTS=true
MD2JSON_CACHE_DELETE_DEBUG_AFTER_DAYS=1
MD2JSON_CACHE_GC_BATCH_LIMIT=500
```

```bash
sudo tee /etc/systemd/system/md2json-cleanup.service >/dev/null <<'EOF'
[Unit]
Description=Clean old md2json job artifacts

[Service]
Type=oneshot
EnvironmentFile=/etc/md2json/md2json.env
WorkingDirectory=/root/workspace/wlm/md2json-api-project
ExecStart=/root/workspace/wlm/md2json-api-project/.venv/bin/python scripts/cleanup_cache.py
EOF
```

```bash
sudo mkdir -p /root/workspace/data/book_prepare/ebooks-md2json-work
sudo mkdir -p /root/workspace/data/book_prepare/ebooks-md2json
```

```bash
cd /root/workspace/wlm/md2json-api-project
set -a
source /etc/md2json/md2json.env
set +a
./.venv/bin/python scripts/md2json_watch_doc2x_folder.py --max-files 1
```

```bash
sudo tee /etc/systemd/system/md2json-mdonly-folder.service >/dev/null <<'EOF'
[Unit]
Description=Run md2json on completed Doc2X folder results
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/md2json/md2json.env
WorkingDirectory=/root/workspace/wlm/md2json-api-project
ExecStart=/root/workspace/wlm/md2json-api-project/.venv/bin/python scripts/md2json_watch_doc2x_folder.py
EOF
```

```bash
sudo tee /etc/systemd/system/md2json-mdonly-folder.timer >/dev/null <<'EOF'
[Unit]
Description=Scan completed Doc2X folder results and run md2json

[Timer]
OnBootSec=10min
OnUnitInactiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart md2json-api.service
sudo systemctl restart md2json-cleanup.timer
sudo systemctl start md2json-cleanup.service
sudo systemctl enable --now md2json-mdonly-folder.timer
sudo systemctl start md2json-mdonly-folder.service
```

```bash
journalctl -u md2json-api.service -n 100 --no-pager
journalctl -u md2json-cleanup.service -n 100 --no-pager
journalctl -u md2json-mdonly-folder.service -n 100 --no-pager
```
