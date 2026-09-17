from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import BACKUP_DIRECTORY_NAME, MODEL_CATALOG_NAME


class CatalogError(RuntimeError):
    pass


def resource_path(relative_path: str) -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / relative_path
    return Path(__file__).resolve().parent.parent / relative_path


def validate_catalog_text(text: str) -> tuple[dict[str, Any], int]:
    if len(text.encode("utf-8")) > 2 * 1024 * 1024:
        raise CatalogError("models.json 不能超过 2 MiB。")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"models.json 格式错误：第 {exc.lineno} 行，第 {exc.colno} 列。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise CatalogError("models.json 根节点必须包含 models 数组。")
    if not payload["models"]:
        raise CatalogError("models.json 至少需要一个模型。")
    slugs: set[str] = set()
    for index, model in enumerate(payload["models"], start=1):
        if not isinstance(model, dict):
            raise CatalogError(f"第 {index} 个模型必须是 JSON 对象。")
        slug = model.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            raise CatalogError(f"第 {index} 个模型缺少 slug。")
        if slug in slugs:
            raise CatalogError(f"模型 slug 重复：{slug}")
        slugs.add(slug)
        for required in ("display_name", "context_window"):
            if required not in model:
                raise CatalogError(f"模型 {slug} 缺少 {required}。")
    return payload, len(payload["models"])


class ModelCatalogManager:
    def __init__(self, codex_home: Path) -> None:
        self.codex_home = codex_home.expanduser().resolve(strict=False)
        self.target_path = self.codex_home / MODEL_CATALOG_NAME
        self.bundled_path = resource_path(f"assets/{MODEL_CATALOG_NAME}")

    def bundled_text(self) -> str:
        try:
            text = self.bundled_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CatalogError(f"无法读取内置 models.json：{exc}") from exc
        validate_catalog_text(text)
        return text

    def current_text(self) -> str:
        if not self.target_path.exists():
            return self.bundled_text()
        try:
            text = self.target_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise CatalogError(f"无法读取 {self.target_path}：{exc}") from exc
        validate_catalog_text(text)
        return text

    def ensure_model_profile(self, model_slug: str) -> tuple[bool, Path | None]:
        """Merge bundled capability metadata for one model without discarding edits."""
        bundled_payload, _ = validate_catalog_text(self.bundled_text())
        bundled_model = next(
            (item for item in bundled_payload["models"] if item.get("slug") == model_slug),
            None,
        )
        if bundled_model is None:
            return False, None
        if not self.target_path.exists():
            _, _, backup = self.inject_bundled()
            return True, backup
        target_payload, _ = validate_catalog_text(self.current_text())
        target_model = next(
            (item for item in target_payload["models"] if item.get("slug") == model_slug),
            None,
        )
        if target_model is None:
            target_payload["models"].append(bundled_model)
        else:
            changed = False
            for key in ("input_modalities", "web_search_tool_type"):
                if key in bundled_model and target_model.get(key) != bundled_model[key]:
                    target_model[key] = bundled_model[key]
                    changed = True
            if not changed:
                return False, None
        backup: Path | None = None
        backup_dir = self.codex_home / BACKUP_DIRECTORY_NAME
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"models-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
        shutil.copy2(self.target_path, backup)
        normalized = json.dumps(target_payload, ensure_ascii=False, indent=4) + "\n"
        temporary = self.target_path.with_name(f"{self.target_path.name}.tmp")
        try:
            temporary.write_text(normalized, encoding="utf-8", newline="\n")
            os.replace(temporary, self.target_path)
            validate_catalog_text(self.target_path.read_text(encoding="utf-8"))
        except Exception as exc:
            if backup is not None:
                shutil.copy2(backup, self.target_path)
            raise CatalogError(f"更新 GLM 模型能力失败：{exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return True, backup

    def save(self, text: str) -> tuple[Path, int, Path | None]:
        payload, count = validate_catalog_text(text)
        normalized = json.dumps(payload, ensure_ascii=False, indent=4) + "\n"
        self.codex_home.mkdir(parents=True, exist_ok=True)
        backup: Path | None = None
        if self.target_path.exists():
            backup_dir = self.codex_home / BACKUP_DIRECTORY_NAME
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = backup_dir / f"models-{stamp}.json"
            shutil.copy2(self.target_path, backup)
        temporary = self.target_path.with_name(f"{self.target_path.name}.tmp")
        try:
            temporary.write_text(normalized, encoding="utf-8", newline="\n")
            os.replace(temporary, self.target_path)
            validate_catalog_text(self.target_path.read_text(encoding="utf-8"))
        except Exception as exc:
            if backup is not None:
                try:
                    shutil.copy2(backup, self.target_path)
                except OSError:
                    pass
            raise CatalogError(f"models.json 保存失败：{exc}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return self.target_path, count, backup

    def inject_bundled(self) -> tuple[Path, int, Path | None]:
        return self.save(self.bundled_text())
