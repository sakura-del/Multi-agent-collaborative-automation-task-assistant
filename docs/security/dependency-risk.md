# 依赖漏洞风险台账（Issue #11 M-5）

> 最后核对：**2026-11-12**，数据源 [OSV](https://osv.dev)（`POST /v1/query`，PyPI 生态），
> 对象是**生产镜像里的实际版本**（`docker exec vp-backend pip list`）。

## 1. 结论先说

安全排查报告（2026-11-11）里那张「21 条 HIGH/CRITICAL」的表**已经过期**：
依赖随版本漂移升到了无公告版本。现查结果只剩 **3 个包、4 个 CVE**，
而且**上游都没有修复版本**（`fixed_in = None`）。

| 包 | 生产版本 | 公告 | 可达性 | 处置 |
| --- | --- | --- | --- | --- |
| `chromadb` | 1.5.9（**已移出生产镜像**） | CVE-2026-45829 / 45830 / 45831 / 45833 | 生产不可达（向量后端是 pgvector） | **移出主依赖**，改为可选 `[chroma]` |
| `python-jose` | 3.5.0 | CVE-2024-33663（算法混淆） | 不可利用：`decode_token` 锁死 `algorithms=[HS256]`，且密钥是 HS256 对称密钥 | 接受（无修复版本；替换 JWT 库成本远大于风险） |
| `ecdsa` | 0.19.2 | CVE-2024-23342（Minerva 计时侧信道） | 不可达：只有 ES\* 系列算法走它，本项目只用 HS256 | 接受（维护者明确不修） |

已随版本升级消除（同一批里原先被点名的）：`certifi` 2026.10.23、`urllib3` 2.8.0、
`aiohttp` 3.14.5、`cryptography` 50.0.2、`pyasn1` 0.6.5、`oauthlib` 4.0.1、
`dnspython` 2.9.2、`filelock` 4.2.10 —— 当前 OSV 查询均无公告。

## 2. 本次改了什么

1. `backend/pyproject.toml`：`chromadb==1.5.9` 从**主依赖**移到可选依赖 `[chroma]`；
2. `docker-compose` 生产编排不装 extras → **生产镜像不再包含 chromadb**（也顺带减小体积）；
3. 代码默认 `rag.vector_backend` 从 `chroma` 改为 **`pgvector`**
   （镜像里没有 chromadb，默认指向它只会在第一次向量操作时炸）；
4. `chroma_backend` 缺包时给可照做的提示：
   `pip install -e '.[chroma]'`，并提示生产用 pgvector；
5. CI 改为 `pip install -e ".[dev,chroma]"`（12 个既有测试走 chroma 内存模式，CI 需要它）；
6. 新增守卫测试 `backend/tests/test_dependency_policy.py`：
   chromadb 不得回到主依赖、`[chroma]` 必须存在、默认后端必须是 pgvector、缺包报错要可执行。

## 3. 判断原则（写下来避免下次抄旧结论）

- **能修就升**；**不能修就看可达性**：不可达 ≠ 不存在风险，但风险可以定价；
- 有未修复 High/Critical 且**生产可达**的包，不允许留在主依赖；
- 每次结论必须带「版本 + 查询日期 + 数据源」，否则下一个人会把历史结论当现状；
- 是否可达要看**代码路径**，不看包名：本项目所有密钥都是 HS256 对称密钥，
  所以 jose/ecdsa 的非对称分支全是死路。

## 4. 怎么重查（复制即可）

```bash
# 1) 生产镜像里的真实版本
ssh root@47.253.81.6 'docker exec vp-backend pip list --format=json' > /tmp/pkgs.json

# 2) 逐个问 OSV（这里只列本次涉及的包；全量建议写脚本批量 querybatch）
curl -s -X POST https://api.osv.dev/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"version":"3.5.0","package":{"name":"python-jose","ecosystem":"PyPI"}}'
```

`fixed_in` 为空表示**上游没有修复版本**——此时「升级」不是选项，只有「移除 / 隔离 / 接受」三条路。

## 5. 仍未处理的延伸项

- `python-jose` 若要彻底消除，只能换 JWT 实现（如 `PyJWT` + 自管 claims），
  属于认证层重构，收益（一条不可利用的公告）不抵成本；
- `ecdsa` 随 `python-jose[cryptography]` 传递进来，等 jose 换实现时自然消失。
