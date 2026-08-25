from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
from typing import Any

import yaml

from .config import AppConfig, EXAMPLE_CONFIG_PATH, USER_CONFIG_PATH


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = _merge_named_list(merged[key], value)
        else:
            merged[key] = value
    return merged


def _merge_named_list(base: list[Any], patch: list[Any]) -> list[Any]:
    result = list(base)
    for patch_item in patch:
        if not isinstance(patch_item, dict) or "name" not in patch_item:
            result.append(patch_item)
            continue
        patch_name = patch_item["name"]
        replaced = False
        for index, base_item in enumerate(result):
            if isinstance(base_item, dict) and base_item.get("name") == patch_name:
                result[index] = _merge_dict(base_item, patch_item)
                replaced = True
                break
        if not replaced:
            result.append(patch_item)
    return result


def preview_config_update(config: AppConfig, patch: dict[str, Any]) -> dict[str, Any]:
    if not patch:
        return config.raw
    return _merge_dict(config.raw, patch)


def apply_config_update(config: AppConfig, patch: dict[str, Any]) -> Path:
    updated = preview_config_update(config, patch)
    target = config.path
    # Never turn the checked-in example into a mutable runtime database. When
    # the app starts before `init`, materialize the user copy on first update.
    if target.resolve() == EXAMPLE_CONFIG_PATH.resolve():
        target = USER_CONFIG_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(EXAMPLE_CONFIG_PATH, target)
    with target.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(updated, handle, allow_unicode=True, sort_keys=False)
    return target
