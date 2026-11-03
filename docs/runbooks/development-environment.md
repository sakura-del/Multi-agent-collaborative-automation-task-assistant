# 服务器开发环境（10.0.10.2）

> 建立于 2026-10-17。用于主系统 A 的日常开发、测试与 Compose 验证。
> 所有命令可在 Windows 终端通过 SSH 执行，也可在服务器终端直接执行。

## 1. 主机与目录

| 项 | 值 |
|---|---|
| 主机 | `ylin@10.0.10.2`（Ubuntu 24.04.4 LTS，192 核，2TB 内存，`/home` 剩余 1.2T） |
| 登录 | 已配置公钥免密（`ssh ylin@10.0.10.2`） |
| 仓库 | `/home/ylin/projects/multi-agent-assistant`（分支 `codex/literature-review`） |
| 工具 | git 2.43、Docker 29.5.2 + Compose v5.1.4、uv 0.9.10、Homebrew、OpenSSL |
| 权限 | 用户在 `docker` 组，可免 sudo 使用 Docker；`sudo` 需要密码 |

## 2. Python 环境

venv 由 uv 创建（CPython 3.11.14），位置 `backend/.venv`：

```bash
cd ~/projects/multi-agent-assistant/backend
.venv/bin/python -m pytest tests -q            # 573 passed, 覆盖率 90.62%
.venv/bin/python -m black --check app tests
.venv/bin/python -m ruff check app tests
.venv/bin/python -m mypy app
```

首次创建命令（已执行，仅换机时参考）：

```bash
cd ~/projects/multi-agent-assistant/backend
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

## 3. Docker Compose

服务器宿主机端口被其他项目大量占用，仓库内新增了**不提交**的 `docker-compose.override.yml`，
只改宿主机映射端口，容器间仍用服务名通信：

| 服务 | 容器端口 | 服务器宿主机端口 |
|---|---|---|
| postgres | 5432 | **35433** |
| redis | 6379 | **36379** |
| chroma | 8000 | **38002** |
| nginx | 80 | **48080** |

```bash
cd ~/projects/multi-agent-assistant
docker compose config            # 校验（.env 已由 .env.example 生成并写入随机 JWT_SECRET_KEY）
docker compose build             # 首次较慢：torch CPU + 前端构建
docker compose up -d
curl -s http://127.0.0.1:48080/api/v1/health/ready
```

`docker-compose.override.yml` 已被 `.gitignore` 忽略；换机时按上表重建。
`.env` 只放在服务器本地（gitignore），LLM 密钥按需填写，禁止提交。

已验证（2026-10-17）：

- 五个容器（postgres / redis / chroma / backend / nginx）全部 `healthy`；
- `GET http://127.0.0.1:48080/` 返回 200，内容为镜像内构建的 Vue 页面；
- `GET /api/v1/health/ready` 返回
  `{"status":"ready","checks":{"database":"ok","redis":"ok","vector":"ok","chroma":"ok"}}`
（`vector` 是与后端无关的稳定键；`chroma` 是 chroma 模式下的兼容别名，
切到 pgvector 后只剩 `vector`）。
- 首次构建约 10–15 分钟（torch CPU + 前端构建），之后重建走缓存会快很多。

## 4. 模型接口（正式实验）

- 模型：`gpt-6-luna`，OpenAI 兼容接口 `https://model.rhzy.ai/v1`（内网 10.0.10.42）
- 密钥只写 `.env` 或进程环境变量，禁止入仓、禁止写入文档
- 可达性检查：`curl --noproxy '*' -sI https://model.rhzy.ai/v1/models`（返回 401/200 即连通）
- **注意**：在 DSH 终端里跑真实模型时要清掉注入的代理变量（`NO_PROXY='*'`），否则
  httpx 0.28.1 会因 `NO_PROXY` 里的 `[::1]` 条目报 `InvalidURL: Invalid port: ':1]'`。
  服务器本机终端不受此影响。

## 5. 代码同步（Windows ↔ 服务器）

服务器没有 GitHub SSH key，而 A 仓库是私有库，当前用 git bundle 同步：

```powershell
# Windows -> 服务器
git bundle create $env:TEMP\maa.bundle --all
scp $env:TEMP\maa.bundle ylin@10.0.10.2:~/
ssh ylin@10.0.10.2 'cd ~/projects/multi-agent-assistant && git fetch ~/maa.bundle "+refs/heads/*:refs/remotes/win/*" && git merge --ff-only win/codex/literature-review'
```

```powershell
# 服务器 -> Windows
ssh ylin@10.0.10.2 'cd ~/projects/multi-agent-assistant && git bundle create /tmp/maa-sync.bundle --all'
scp ylin@10.0.10.2:/tmp/maa-sync.bundle $env:TEMP\maa-sync.bundle
git fetch $env:TEMP\maa-sync.bundle "+refs/heads/*:refs/remotes/server/*"
```

可选改进：把服务器 `~/.ssh/id_ed25519.pub` 加到 GitHub（Deploy Key 只读，或账号 SSH key），
之后就可以直接在服务器 `git pull` / `git push`，省掉 bundle。

## 6. Windows 侧已知问题

- `E:\Multi-agent collaborative automation task assistant` 目录 ACL 异常：workspace-write 沙箱
  无法预置写权限，需会话保持「完全权限」。
- `%TEMP%\pytest-of-Lenovo` 权限被历史进程写坏：本地 pytest 一律加
  `--basetemp="$env:TEMP\pytest-dsh"`，否则 26 个用例 setup 阶段 `PermissionError`。
- DSH 终端注入代理变量，见 §4。

## 7. B 基线仓库（本地 Windows）

- 位置：`C:\Users\Lenovo\Documents\Codex\2026-09-30\new-chat\outputs\multi-agent-research-thesis`
- 状态：已冻结；Q1/Q2/Q3 三次运行均 `completed`，commit `7a0cd9b6`，tag `baseline-v1` 已推送；
  运行清单见仓库内 `thesis/runs/README.md`
- Q3 补跑：`python -m thesis.run_baseline --question q3_evaluation_design --timeout 1800`
- 从 DSH 终端运行时必须绕开代理变量：用 `%TEMP%\run_q3_clean.py` 包装（已创建），
  否则同样触发 `NO_PROXY` + httpx 解析错误。
