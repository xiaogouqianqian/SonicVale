import os
import shutil
import sys
from pathlib import Path


def _is_writable_directory(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = Path(path) / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _get_portable_data_path() -> str | None:
    env_path = os.environ.get("SONICVALE_DATA_DIR")
    if env_path:
        return env_path

    portable_exec_dir = os.environ.get("PORTABLE_EXECUTABLE_DIR")
    if portable_exec_dir:
        return os.path.join(portable_exec_dir, "userdata")

    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent / "userdata")

    project_root = Path(__file__).resolve().parents[3]
    return str(project_root / "userdata")


# 得到默认配置文件
def getConfigPath():
    portable_dir = _get_portable_data_path()
    if portable_dir and _is_writable_directory(portable_dir):
        return portable_dir

    # 回退到用户目录，兼容开发环境和不可写目录
    user_dir = os.path.join(os.path.expanduser("~"), "SonicVale")
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def getFfmpegPath():
    base_dir = Path(getattr(sys, "_MEIPASS", Path(os.path.abspath("."))))
    candidates = [
        base_dir / "app" / "core" / "ffmpeg" / "ffmpeg.exe",
        base_dir / "core" / "ffmpeg" / "ffmpeg.exe",
        base_dir / "ffmpeg.exe",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    return str(candidates[0])