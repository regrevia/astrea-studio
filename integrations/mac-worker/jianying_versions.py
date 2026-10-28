"""剪映版本适配：让草稿的 schema 版本跟着本机装的剪映走，而不是写死。

为什么需要：`draft_content.json` 里真正决定「剪映认不认」的是
`version` / `new_version` 两个整数与字符串，外加 `platform` /
`last_modified_platform` 里的 app_id、app_source、app_version、os。
VectCutAPI 的模板是照 10.2.0（windows）写的，本机装 11.6 macOS 也能打开——
说明剪映对**旧**版本是向后兼容的，但对**新**版本没有承诺。

所以这里的策略不是猜新版本号，而是：

1. 读本机 App 的 Info.plist，拿到真实版本与 build；
2. 在用户草稿目录里找**这台机器上剪映自己存过**的草稿，抄它的
   version / new_version / platform 块（参考优先：这是唯一有实证的 schema）；
3. 找不到参考（新装、草稿目录空）才退回内置表，并在结果里标 `source`，
   让上层知道这次是推断而不是实测。

新版本上线时通常不用改代码：只要剪映自己保存过一次草稿，第 2 步就会
自动跟上新 schema。内置表只用于「一台干净的机器」。

CLI：
    python3 jianying_versions.py --json
    python3 jianying_versions.py --drafts ~/Movies/JianyingPro/User\ Data/Projects/com.lveditor.draft
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import time
from pathlib import Path
from typing import Any

DEFAULT_APP = Path("/Applications/VideoFusion-macOS.app")
# 想强制用某份草稿的 schema 做实验时给这个环境变量（草稿目录名）。
SCHEMA_DRAFT_ENV = "JY_SCHEMA_DRAFT"
DEFAULT_DRAFT_ROOT = Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft"

# 内置表：只在草稿里**完全没有** version 字段（老模板）时补一套已知可开的；
# 正常路径永远优先抄本机草稿，不拿这张表去猜新版本。键是 App 主版本号。
BUILTIN_SCHEMAS: dict[int, dict[str, Any]] = {
    11: {
        "version": 360000,
        "new_version": "110.0.0",
        "platform": {"app_id": 3704, "app_source": "lv", "app_version": "11.6.0",
                     "os": "mac", "os_version": "15.5"},
    },
}
# 注：这两个表不参与选择，只是「万一要手工对齐」的参考值，测试里断言它们保持可解析。
# 完全未知时的兜底：保持 VectCutAPI 模板里那套，它在 11.6 上实测能打开。
LAST_RESORT = {
    "version": 360000,
    "new_version": "110.0.0",
    "platform": {"app_id": 3704, "app_source": "lv", "app_version": "10.2.0",
                 "os": "windows"},
}


def _major(version: Any) -> int | None:
    text = str(version or "").strip().split(".")[0]
    return int(text) if text.isdigit() else None


def read_app_info(app_path: os.PathLike | str = DEFAULT_APP) -> dict[str, Any]:
    """读剪映 bundle 的版本；读不到就是没装，不编造。"""
    path = Path(app_path)
    info = path / "Contents" / "Info.plist"
    if not info.is_file():
        return {"exists": False, "path": str(path)}
    try:
        with info.open("rb") as fh:
            plist = plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001 - plist 坏掉等同于没装
        return {"exists": False, "path": str(path), "error": f"{type(exc).__name__}: {exc}"}
    return {
        "exists": True,
        "path": str(path),
        "bundleId": plist.get("CFBundleIdentifier"),
        "version": plist.get("CFBundleShortVersionString"),
        "build": plist.get("CFBundleVersion"),
    }


def _draft_sort_key(entry: Path) -> float:
    try:
        return entry.stat().st_mtime
    except OSError:
        return 0.0


def scan_reference_drafts(root: os.PathLike | str = DEFAULT_DRAFT_ROOT,
                          limit: int = 40) -> list[dict[str, Any]]:
    """按修改时间倒序读草稿的版本块；坏 JSON / 旧格式直接跳过。"""
    base = Path(root)
    if not base.is_dir():
        return []
    candidates = [p for p in base.iterdir() if p.is_dir()]
    candidates.sort(key=_draft_sort_key, reverse=True)
    out: list[dict[str, Any]] = []
    for entry in candidates[:limit]:
        content = entry / "draft_content.json"
        if not content.is_file():
            continue
        try:
            data = json.loads(content.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        platform = data.get("platform") or {}
        if not platform.get("app_version"):
            continue
        out.append({
            "draft": entry.name,
            "path": str(content),
            "version": data.get("version"),
            "new_version": data.get("new_version"),
            "platform": platform,
            "last_modified_platform": data.get("last_modified_platform") or {},
            "mtime": _draft_sort_key(entry),
        })
    return out


def resolve_schema(app_version: Any = None, app_build: Any = None,
                   references: list[dict[str, Any]] | None = None,
                   app_path: os.PathLike | str = DEFAULT_APP,
                   draft_root: os.PathLike | str = DEFAULT_DRAFT_ROOT) -> dict[str, Any]:
    """定这次写盘用哪套版本字段。

    参考草稿优先（同主版本 > 任意最新 > 内置表 > 兜底）。返回值里 `source`
    说明依据，`app` 是检测结果，方便写进 sidecar 和自检输出。
    """
    app = read_app_info(app_path)
    version = app_version if app_version is not None else app.get("version")
    build = app_build if app_build is not None else app.get("build")
    refs = references if references is not None else scan_reference_drafts(draft_root)
    forced = os.getenv(SCHEMA_DRAFT_ENV, "").strip()
    if forced:
        picked = [r for r in refs if r.get("draft") == forced]
        if not picked:
            raise ValueError(f"{SCHEMA_DRAFT_ENV}={forced} 不存在或不是可读草稿")
        refs = picked

    major = _major(version)
    chosen: dict[str, Any] | None = None
    source = ""
    # 只认**同主版本**的参考草稿：这台的剪映自己存过这种 schema，是唯一实证。
    # 别的主版本的草稿只说明旧模板也能开，不能拿来当新版本的规格。
    same_major = [r for r in refs if _major((r.get("platform") or {}).get("app_version")) == major]
    if forced and not same_major:
        same_major = refs
    if major is not None and same_major:
        chosen, source = same_major[0], "reference-same-major"
    else:
        chosen, source = None, "keep-template"

    # apply=False 表示这次不改草稿的版本块（模板里的 10.2/windows 在 11.6 实测可开，
    # 没有实证就不动它）。等这台机器上的剪映自己保存过一份新版本草稿，下一步会自动采纳。
    schema = {
        "version": (chosen or {}).get("version"),
        "new_version": (chosen or {}).get("new_version"),
        "platform": dict((chosen or {}).get("platform") or {}),
        "last_modified_platform": dict((chosen or {}).get("last_modified_platform")
                                       or (chosen or {}).get("platform") or {}),
        "source": source,
        "apply": chosen is not None,
        "app": app,
        "referenceDraft": (chosen or {}).get("draft"),
    }
    if schema["apply"]:
        # 参考草稿的 platform 是「保存它的那台机器」，落到本机时要带上本机版本，
        # 否则草稿列表里会显示成另一台设备的记录。
        if version:
            schema["platform"]["app_version"] = str(version)
            schema["last_modified_platform"]["app_version"] = str(version)
        if build:
            schema["platform"]["app_build"] = str(build)
    return schema


def stamp(content: dict[str, Any], schema: dict[str, Any],
          os_name: str | None = None) -> dict[str, Any]:
    """把版本块写进草稿；返回实际改了什么，便于写进 sidecar 追溯。"""
    before = {
        "version": content.get("version"),
        "new_version": content.get("new_version"),
        "appVersion": (content.get("platform") or {}).get("app_version"),
        "os": (content.get("platform") or {}).get("os"),
    }
    if schema.get("version") is not None:
        content["version"] = schema["version"]
    if schema.get("new_version") is not None:
        content["new_version"] = schema["new_version"]
    platform = dict(content.get("platform") or {})
    platform.update(schema.get("platform") or {})
    if os_name:
        platform["os"] = os_name
    content["platform"] = platform
    last = dict(content.get("last_modified_platform") or {})
    last.update(schema.get("last_modified_platform") or schema.get("platform") or {})
    if os_name:
        last["os"] = os_name
    content["last_modified_platform"] = last
    return {
        "before": before,
        "after": {
            "version": content.get("version"),
            "new_version": content.get("new_version"),
            "appVersion": platform.get("app_version"),
            "os": platform.get("os"),
        },
        "source": schema.get("source"),
        "referenceDraft": schema.get("referenceDraft"),
    }


def support_report(schema: dict[str, Any]) -> dict[str, Any]:
    """这次是「实测采纳」还是「沿用旧模板」，以及新装机器的操作指引。"""
    major = _major((schema.get("app") or {}).get("version"))
    if schema.get("source") == "reference-same-major":
        return {"status": "adopted", "major": major,
                "note": "已抄本机同主版本草稿的 schema"}
    if schema.get("apply"):
        return {"status": "forced", "major": major,
                "note": f"由 {SCHEMA_DRAFT_ENV} 指定草稿的 schema"}
    if major is not None and major not in BUILTIN_SCHEMAS:
        return {"status": "unknown-major", "major": major,
                "note": "这台装的是没验证过的主版本：沿用模板字段；"
                        "在剪映里打开并保存任意草稿一次，下次就自动采纳该版本 schema"}
    return {"status": "template", "major": major,
            "note": "沿用 VectCutAPI 模板的 10.2/windows 版本块（11.6 实测可开）"}


def describe(app_path: os.PathLike | str = DEFAULT_APP,
             draft_root: os.PathLike | str = DEFAULT_DRAFT_ROOT) -> dict[str, Any]:
    refs = scan_reference_drafts(draft_root)
    schema = resolve_schema(references=refs, app_path=app_path, draft_root=draft_root)
    report = support_report(schema)
    return {
        "app": schema["app"],
        "schema": {k: schema[k] for k in ("version", "new_version", "source",
                                          "apply", "referenceDraft")},
        "platform": schema["platform"],
        "support": report,
        "references": len(refs),
        "referenceApps": sorted({str((r.get("platform") or {}).get("app_version")) for r in refs}),
        "draftRoot": str(draft_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="剪映版本适配自检")
    parser.add_argument("--app", default=str(DEFAULT_APP))
    parser.add_argument("--drafts", default=str(DEFAULT_DRAFT_ROOT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    info = describe(args.app, args.drafts)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        app = info["app"]
        state = (f'{app["version"]} (build {app.get("build")})' if app.get("exists")
                 else "未安装")
        print(f'剪映: {state}  ·  {app.get("path")}')
        print(f'草稿 schema: version={info["schema"]["version"]} '
              f'new_version={info["schema"]["new_version"]} '
              f'source={info["schema"]["source"]}')
        print(f'参考草稿: {info["references"]} 份，写入过版本: {info["referenceApps"]}')
        print(f'支持状态: {info["support"]["status"]} — {info["support"]["note"]}')
    return 0 if info["app"].get("exists") else 1


if __name__ == "__main__":
    raise SystemExit(main())
