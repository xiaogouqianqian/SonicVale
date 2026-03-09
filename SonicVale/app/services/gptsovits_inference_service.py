import json
import os
import shutil
from dataclasses import dataclass
from typing import Any


@dataclass
class GPTSoVITSModelInfo:
    name: str
    model_dir: str
    infer_config_path: str


class GPTSoVITSInferenceService:
    """Utilities for managing GPT-SoVITS-Inference local model folders."""

    def validate_project_path(self, project_path: str) -> tuple[bool, str]:
        if not project_path:
            return False, "project_path 不能为空"
        if not os.path.isdir(project_path):
            return False, f"路径不存在: {project_path}"

        trained_dir = self._trained_dir(project_path)
        if not os.path.isdir(trained_dir):
            return False, f"未找到 trained 目录: {trained_dir}"

        return True, "路径有效"

    def scan_models(self, project_path: str) -> list[GPTSoVITSModelInfo]:
        trained_dir = self._trained_dir(project_path)
        if not os.path.isdir(trained_dir):
            return []

        result: list[GPTSoVITSModelInfo] = []
        for name in sorted(os.listdir(trained_dir)):
            model_dir = os.path.join(trained_dir, name)
            if not os.path.isdir(model_dir):
                continue
            infer_config_path = os.path.join(model_dir, "infer_config.json")
            if not os.path.isfile(infer_config_path):
                continue
            result.append(
                GPTSoVITSModelInfo(
                    name=name,
                    model_dir=model_dir,
                    infer_config_path=infer_config_path,
                )
            )
        return result

    def import_model(self, project_path: str, source_model_dir: str) -> str:
        if not source_model_dir or not os.path.isdir(source_model_dir):
            raise ValueError(f"模型目录不存在: {source_model_dir}")

        infer_config = os.path.join(source_model_dir, "infer_config.json")
        if not os.path.isfile(infer_config):
            raise ValueError("模型目录缺少 infer_config.json")

        trained_dir = self._trained_dir(project_path)
        os.makedirs(trained_dir, exist_ok=True)

        model_name = os.path.basename(os.path.normpath(source_model_dir))
        target_dir = os.path.join(trained_dir, model_name)
        if os.path.exists(target_dir):
            raise ValueError(f"目标模型已存在: {target_dir}")

        shutil.copytree(source_model_dir, target_dir)
        return target_dir

    def read_infer_config(self, infer_config_path: str) -> dict[str, Any]:
        try:
            with open(infer_config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def _trained_dir(project_path: str) -> str:
        return os.path.join(project_path, "trained")
