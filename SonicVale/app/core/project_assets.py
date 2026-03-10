import hashlib
import os


PROJECT_MODE_STANDARD = "standard"
PROJECT_MODE_AUDIO_EPUB = "audio_epub"


def normalize_project_mode(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text == PROJECT_MODE_AUDIO_EPUB:
        return PROJECT_MODE_AUDIO_EPUB
    return PROJECT_MODE_STANDARD


def get_project_dir(project_root_path: str, project_id: int) -> str:
    return os.path.join(project_root_path, str(project_id))


def ensure_project_structure(project_root_path: str, project_id: int) -> dict:
    project_dir = get_project_dir(project_root_path, project_id)
    source_dir = os.path.join(project_dir, "source")
    export_dir = os.path.join(project_dir, "exports")
    cache_dir = os.path.join(project_dir, "cache")
    unpacked_dir = os.path.join(source_dir, "unpacked")

    for path in [project_dir, source_dir, export_dir, cache_dir, unpacked_dir]:
        os.makedirs(path, exist_ok=True)

    return {
        "project_dir": project_dir,
        "source_dir": source_dir,
        "export_dir": export_dir,
        "cache_dir": cache_dir,
        "unpacked_dir": unpacked_dir,
        "source_epub_path": os.path.join(source_dir, "original.epub"),
    }


def compute_file_sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()