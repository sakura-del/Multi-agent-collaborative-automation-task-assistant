"""生产账号卫生 CLI：盘点弱口令、轮换口令、清理测试账号（Issue #10）。

## 背景

2026-11-11 安全排查（`docs/security/2026-11-11-security-audit.md` H-1/H-2）实测：

- `demo` 用公开在仓库文档里的口令就能登录，且 `role=admin`
  （名下 86 个任务、11 篇文档、4 个工作区、1 个 API Key、1 个订阅）；
- `e2efull` / `e2ereconn` 的口令同样公开，实测都能登录；
- 库里另有二十多个历史测试账号（`exitgate*` / `literature-demo*` / `rh*` /
  `p1*` / `i2*` / `scr_*` / `final*` / `diag-user`）。

改文档解决不了存量：口令还在库里有效。所以需要一次**数据侧**操作，
而数据侧操作必须"能手抖、能回查"，于是有了这个脚本。

## 三条安全设计

1. **默认 dry-run**：`purge` 不带 `--apply` 只打印清单与拒绝理由，不写库；
2. **删除前有硬规则**：`demo` 永远受保护、任何 `admin` 一律拒删、
   名字不像测试账号的一律拒删（防拼写错误删到真人）；
3. **删完必须回查**：按 AGENTS §5 #11，脚本自己再查一遍
   「账号是否还在 / 孤儿向量是否为 0」，而不是打印一句"已清理"。

## 向量表为什么要单独清

`knowledge_vectors` / `knowledge_vectors_bge` 是**裸表**：没有外键指向
`knowledge_doc`，删文档不会级联清向量。漏清 = 留下孤儿向量，
检索时可能命中已删除文档的内容。所以 `purge` 先按 `user_id` 删向量，再删账号。

## 用法

```bash
# 1) 盘点（只读）。--leaked-passwords-file 是本地文件，不要把口令写进命令行历史
python -m scripts.account_hygiene audit --leaked-passwords-file /root/.weak-passwords

# 2) 轮换（强随机口令写到 600 文件，不回显）
python -m scripts.account_hygiene rotate --username demo --out-file /opt/veripaper/.demo-password

# 3) 清理（先看清单）
python -m scripts.account_hygiene purge --usernames e2efull,e2ereconn,exitgate1792241768

# 4) 清理（真正执行；执行后脚本自动回查）
python -m scripts.account_hygiene purge --usernames e2efull,e2ereconn,exitgate1792241768 --apply
```
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import string
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 只为类型标注，运行时按需在函数内导入（脚本需先有环境变量）
    from sqlalchemy.ext.asyncio import AsyncSession

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 演示账号承载全部历史数据与前端演示入口，任何情况下都不参与清理
PROTECTED_USERNAMES: frozenset[str] = frozenset({"demo"})

# 历史测试账号的命名规律（见安全报告 H-2）。
# 刻意写成"字面前缀 + 数字/短哈希"而不是宽松前缀：
# `rh*` / `p1*` 这种写法会把 `rhino`、`p1` 一起吃掉，而删除是不可逆的。
TEST_USERNAME_PATTERNS: tuple[str, ...] = (
    r"exitgate\d{6,}",
    r"literature-demo\d*",
    r"literature-gate\d*",
    r"rh\d{6,}",
    r"p1\d{6,}",
    r"i2\d{6,}",
    r"scr_[0-9a-f]{8}",
    r"final\d{6,}",
    r"e2e(full|reconn)?",
    r"diag-user",
)

_COMPILED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern) for pattern in TEST_USERNAME_PATTERNS
)

# 抄写到 .env 时最易混淆的字符不参与生成
_AMBIGUOUS = set("0O1lI")
_LOWER = "".join(ch for ch in string.ascii_lowercase if ch not in _AMBIGUOUS)
_UPPER = "".join(ch for ch in string.ascii_uppercase if ch not in _AMBIGUOUS)
_DIGITS = "".join(ch for ch in string.digits if ch not in _AMBIGUOUS)
_SYMBOLS = "!@#$%^&*-_=+"
PASSWORD_ALPHABET = _LOWER + _UPPER + _DIGITS + _SYMBOLS
MIN_PASSWORD_LENGTH = 12

# 只允许这样的标识符拼进 SQL（表名来自配置/系统目录，仍要拦一道）
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class UserRow:
    """一个账号的盘点行：身份字段 + 名下资源计数。"""

    username: str
    role: str
    is_active: bool
    tasks: int
    knowledge_docs: int
    workspaces: int
    api_keys: int
    subscriptions: int
    created_at: str
    user_id: str = ""


@dataclass(frozen=True)
class PurgePlan:
    """清理计划：能删的与不能删的（附理由）分开给。"""

    targets: tuple[UserRow, ...]
    refusals: tuple[str, ...]


def generate_password(length: int = 32) -> str:
    """生成强随机口令：四类字符齐全、排除易混字符、用 `secrets` 取随机源。

    为什么要保证类别齐全：不少系统的口令复杂度校验按类别判断，
    生成一个"全小写 40 位"仍然会被拒。
    """
    if length < MIN_PASSWORD_LENGTH:
        raise ValueError(f"口令长度不得小于 {MIN_PASSWORD_LENGTH}")

    required = [
        secrets.choice(_LOWER),
        secrets.choice(_UPPER),
        secrets.choice(_DIGITS),
        secrets.choice(_SYMBOLS),
    ]
    rest = [secrets.choice(PASSWORD_ALPHABET) for _ in range(length - len(required))]
    password = required + rest
    # 固定"前四位必是四类"会让口令模式可预测，打散
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def is_test_account(username: str) -> bool:
    """是否命中历史测试账号的命名规律（不代表可以删——还要过角色/保护规则）。"""
    return any(pattern.fullmatch(username) for pattern in _COMPILED_PATTERNS)


def select_vector_tables(existing: Sequence[str], configured: str) -> tuple[str, ...]:
    """选出需要按 user_id 清向量的表。

    生产同时存在新旧两张向量表（`knowledge_vectors`、`knowledge_vectors_bge`），
    切换只改了读写目标，旧表数据仍在。两张都没有外键，必须显式清。
    """
    names = {configured}
    names.update(name for name in existing if name.startswith("knowledge_vectors"))
    return tuple(sorted(names))


def plan_purge(rows: Sequence[UserRow], requested: Sequence[str]) -> PurgePlan:
    """按硬规则生成清理计划；任何一条拒绝理由都会让执行直接中止。"""
    index = {row.username: row for row in rows}
    targets: list[UserRow] = []
    refusals: list[str] = []
    seen: set[str] = set()

    for username in requested:
        if username in seen:
            continue
        seen.add(username)

        row = index.get(username)
        if row is None:
            refusals.append(f"{username}：账号不存在（拼写错误？）")
            continue
        if username in PROTECTED_USERNAMES:
            refusals.append(f"{username}：受保护账号，不参与清理")
            continue
        if row.role.strip().lower() == "admin":
            refusals.append(f"{username}：角色是 admin，需人工确认后单独处理")
            continue
        if not is_test_account(username):
            refusals.append(f"{username}：名字不像测试账号，拒绝自动清理")
            continue

        targets.append(row)

    return PurgePlan(targets=tuple(targets), refusals=tuple(refusals))


# ===== 数据库操作 =====


async def _load_user_rows(session: AsyncSession) -> list[UserRow]:
    """盘点：一次查出全部账号与名下资源计数（账号量级几十，逐条计数可接受）。"""
    from sqlalchemy import ColumnElement, func, select

    from app.db.models.api_key import ApiKey
    from app.db.models.billing import Subscription
    from app.db.models.knowledge import KnowledgeDoc
    from app.db.models.task import Task
    from app.db.models.user import User
    from app.db.models.workspace import Workspace

    async def count(column: ColumnElement[Any]) -> dict[Any, int]:
        """按某一列分组计数，一次查询取回全表分布（避免 N+1）。"""
        result = await session.execute(select(column, func.count()).group_by(column))
        return {row[0]: int(row[1]) for row in result}

    task_counts = await count(Task.user_id)
    doc_counts = await count(KnowledgeDoc.user_id)
    ws_counts = await count(Workspace.owner_id)
    key_counts = await count(ApiKey.user_id)
    sub_counts = await count(Subscription.user_id)

    result = await session.execute(select(User).order_by(User.role, User.username))
    rows: list[UserRow] = []
    for user in result.scalars():
        rows.append(
            UserRow(
                username=user.username,
                role=user.role,
                is_active=bool(user.is_active),
                tasks=task_counts.get(user.id, 0),
                knowledge_docs=doc_counts.get(user.id, 0),
                workspaces=ws_counts.get(user.id, 0),
                api_keys=key_counts.get(user.id, 0),
                subscriptions=sub_counts.get(user.id, 0),
                created_at=user.created_at.strftime("%Y-%m-%d") if user.created_at else "",
                user_id=str(user.id),
            )
        )
    return rows


async def _existing_vector_tables(session: AsyncSession) -> list[str]:
    """读出库里真实存在的 public 表名（给 `select_vector_tables` 用）。"""
    from sqlalchemy import text

    result = await session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    )
    return [str(row[0]) for row in result]


def _print_table(rows: Sequence[UserRow], weak: dict[str, bool]) -> None:
    header = (
        f"{'账号':<22}{'角色':<7}{'活跃':<5}{'任务':>5}{'文档':>5}{'工作区':>6}"
        f"{'KEY':>4}{'订阅':>5}{'弱口令':>7}  创建"
    )
    print(header)
    print("-" * 88)
    for row in rows:
        flag = "!! 命中" if weak.get(row.username) else ""
        print(
            f"{row.username:<22}{row.role:<7}{'Y' if row.is_active else 'N':<5}"
            f"{row.tasks:>5}{row.knowledge_docs:>5}{row.workspaces:>6}"
            f"{row.api_keys:>4}{row.subscriptions:>5}{flag:>7}  {row.created_at}"
        )


def _load_leaked_passwords(path: Path | None) -> list[str]:
    """从本地文件读取已泄露口令清单（一行一个）。

    密码不写进命令行：`ps` / shell history / 终端回显都会留下痕迹。
    """
    if path is None:
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def audit(leaked_file: Path | None) -> int:
    from sqlalchemy import select

    from app.core.security import verify_password
    from app.db.models.user import User
    from app.db.session import AsyncSessionLocal

    leaked = _load_leaked_passwords(leaked_file)

    async with AsyncSessionLocal() as session:
        rows = await _load_user_rows(session)
        weak: dict[str, bool] = {}
        if leaked:
            result = await session.execute(select(User))
            for user in result.scalars():
                weak[user.username] = any(
                    verify_password(candidate, user.password_hash) for candidate in leaked
                )

    _print_table(rows, weak)
    print()
    print(f"账号总数：{len(rows)}；弱口令命中：{sum(1 for v in weak.values() if v)} 个")
    if not leaked:
        print("提示：未提供 --leaked-passwords-file，本次不做弱口令比对")
    return 0


async def rotate(username: str, out_file: Path | None, length: int) -> int:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.models.user import User
    from app.db.session import AsyncSessionLocal

    password = generate_password(length)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            print(f"[rotate] FAIL 账号不存在：{username}", file=sys.stderr)
            return 2

        # **先落盘，再改库**（2026-11-11 实测踩坑）：
        # 首版先 commit 再写文件，容器里写 /opt/veripaper（未挂载）直接 FileNotFoundError，
        # 结果口令已经改了、新口令却没人知道——等于把账号锁死。
        # 顺序反过来，写不出文件就根本不动库。
        if out_file is not None:
            try:
                out_file.write_text(password + "\n", encoding="utf-8")
                out_file.chmod(0o600)
            except OSError as exc:
                print(
                    f"[rotate] FAIL 口令无法落盘（{out_file}）：{exc}；数据库未修改",
                    file=sys.stderr,
                )
                return 2

        # token_invalid_before = 现在：让此前签发的 access / refresh 全部失效，
        # 否则"改了口令但攻击者手上的 7 天 refresh token 仍然可用"。
        user.password_hash = hash_password(password)
        user.token_invalid_before = datetime.now(UTC)
        await session.commit()

        role = user.role
        is_active = bool(user.is_active)

    print(f"[rotate] OK 账号={username} 角色={role} 活跃={is_active} 长度={len(password)}")
    print(
        f"[rotate] 口令已写入 {out_file}" if out_file else "[rotate] 未指定 --out-file，口令未落盘"
    )
    print("[rotate] 已写入 token_invalid_before：此前的令牌全部失效")
    return 0


async def purge(usernames: Sequence[str], apply: bool) -> int:
    from sqlalchemy import delete, text

    from app.core.config import settings
    from app.db.models.user import User
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = await _load_user_rows(session)
        plan = plan_purge(rows, usernames)

        print(f"[purge] 待清理 {len(plan.targets)} 个账号：")
        for row in plan.targets:
            print(
                f"  - {row.username:<22} 任务={row.tasks:<3} 文档={row.knowledge_docs:<3} "
                f"工作区={row.workspaces:<2} 最后创建={row.created_at}"
            )
        if plan.refusals:
            print("\n[purge] 拒绝执行，理由：")
            for reason in plan.refusals:
                print(f"  ! {reason}")
            print("[purge] 未做任何修改", file=sys.stderr)
            return 2

        if not plan.targets:
            print("[purge] 没有可清理的账号（请求为空或全部未命中）")
            return 0

        if not apply:
            print("\n[purge] dry-run：未写库。加 --apply 执行删除")
            return 0

        vector_tables = select_vector_tables(
            await _existing_vector_tables(session), settings.rag.pgvector_table
        )
        for table in vector_tables:
            if not _SAFE_IDENTIFIER.fullmatch(table):
                print(f"[purge] FAIL 非法表名：{table}", file=sys.stderr)
                return 2

        print(f"\n[purge] 向量表：{', '.join(vector_tables) or '（无）'}")
        deleted_vectors: dict[str, int] = {}
        for row in plan.targets:
            total = 0
            for table in vector_tables:
                # 表名来自系统目录/配置，且已在上面用 _SAFE_IDENTIFIER 白名单校验，
                # 不存在拼接注入；用户 id 走参数绑定。
                result = await session.execute(
                    text(f'DELETE FROM "{table}" WHERE user_id = :uid'),
                    {"uid": row.user_id},
                )
                total += int(getattr(result, "rowcount", 0) or 0)
            deleted_vectors[row.username] = total
            await session.execute(delete(User).where(User.id == row.user_id))

        await session.commit()
        print(
            f"[purge] 已提交：删除 {len(plan.targets)} 个账号，"
            f"向量 {sum(deleted_vectors.values())} 条"
        )

    # ===== 回查（AGENTS §5 #11：不能只打印成功）=====
    return await _verify_purge([row.username for row in plan.targets], deleted_vectors)


async def _verify_purge(usernames: Sequence[str], deleted_vectors: dict[str, int]) -> int:
    from sqlalchemy import select, text

    from app.core.config import settings
    from app.db.models.user import User
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User.username).where(User.username.in_(usernames)))
        remaining = [row[0] for row in result]

        orphan = 0
        tables = select_vector_tables(
            await _existing_vector_tables(session), settings.rag.pgvector_table
        )
        # 用账号名反查 uuid 已不可能（账号已删），按删除时记录的 uid 统计
        # —— 这里换一种更狠的问法：全库是否还有指向"已不存在的用户"的向量
        for table in tables:
            if not _SAFE_IDENTIFIER.fullmatch(table):
                continue
            # 表名同上已白名单校验；这里刻意"反向"查全库孤儿，
            # 比只查本次删掉的 user_id 更能兜住历史遗留的脏数据。
            check = await session.execute(
                text(
                    f'SELECT count(*) FROM "{table}" v '
                    "WHERE v.user_id <> '' AND NOT EXISTS "
                    '  (SELECT 1 FROM "user" u WHERE CAST(u.id AS TEXT) = v.user_id)'
                )
            )
            orphan += int(check.scalar_one())

    print()
    print(f"[verify] 残留账号：{len(remaining)} 个（期望 0）{remaining if remaining else ''}")
    print(f"[verify] 全库孤儿向量：{orphan} 条（期望 0）")
    print(f"[verify] 本次删除向量合计：{sum(deleted_vectors.values())} 条")

    if remaining or orphan:
        print("[verify] FAIL", file=sys.stderr)
        return 1
    print("[verify] OK")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.account_hygiene",
        description="生产账号卫生：盘点弱口令 / 轮换口令 / 清理测试账号",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit_parser = sub.add_parser("audit", help="盘点账号与名下资源（只读）")
    audit_parser.add_argument(
        "--leaked-passwords-file",
        type=Path,
        default=None,
        help="已泄露口令清单（一行一个），用于比对哪些账号仍在用旧口令",
    )
    audit_parser.add_argument("--json", action="store_true", help="以 JSON 输出，便于留档")

    rotate_parser = sub.add_parser("rotate", help="把某账号口令轮换为强随机口令")
    rotate_parser.add_argument("--username", required=True)
    rotate_parser.add_argument("--out-file", type=Path, default=None, help="口令写入该文件（600）")
    rotate_parser.add_argument("--length", type=int, default=32)

    purge_parser = sub.add_parser("purge", help="清理测试账号（默认 dry-run）")
    purge_parser.add_argument(
        "--usernames",
        required=True,
        help="逗号分隔的账号名；写全名而不是通配，避免误删",
    )
    purge_parser.add_argument("--apply", action="store_true", help="真正执行删除")

    return parser.parse_args(argv)


async def audit_json(leaked_file: Path | None) -> int:
    """盘点并以 JSON 输出（便于留档/比对），字段与表格一致外加两个判定位。"""
    from sqlalchemy import select

    from app.core.security import verify_password
    from app.db.models.user import User
    from app.db.session import AsyncSessionLocal

    leaked = _load_leaked_passwords(leaked_file)

    async with AsyncSessionLocal() as session:
        rows = await _load_user_rows(session)
        hashes = {
            user.username: user.password_hash
            for user in (await session.execute(select(User))).scalars()
        }

    payload = []
    for row in rows:
        item = asdict(row)
        item["weak_password"] = any(
            verify_password(candidate, hashes.get(row.username, "")) for candidate in leaked
        )
        item["test_account"] = is_test_account(row.username)
        payload.append(item)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


async def main_async(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.command == "audit":
        if args.json:
            return await audit_json(args.leaked_passwords_file)
        return await audit(args.leaked_passwords_file)

    if args.command == "rotate":
        return await rotate(args.username, args.out_file, args.length)

    if args.command == "purge":
        usernames = [item.strip() for item in args.usernames.split(",") if item.strip()]
        return await purge(usernames, args.apply)

    return 2


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(main_async(argv))
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
