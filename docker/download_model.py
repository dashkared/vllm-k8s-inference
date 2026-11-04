"""
Model Downloader & Cache Validator for Kubernetes Persistent Volumes.

Checks whether the target model weights already exist on the mounted PVC.
If found, skips downloading to ensure offline-ready restarts.
If missing, downloads weights from Hugging Face using the provided HF_TOKEN.
"""

import os
import sys
import glob
from huggingface_hub import snapshot_download


def check_model_cached(model_dir: str) -> bool:
    """
    Validates if model weights and configurations are already present locally.
    """
    if not os.path.exists(model_dir):
        return False

    config_path = os.path.join(model_dir, "config.json")
    if not os.path.isfile(config_path) or os.path.getsize(config_path) == 0:
        return False

    # Check for weights (.safetensors or .bin)
    weights = glob.glob(os.path.join(model_dir, "*.safetensors")) + glob.glob(
        os.path.join(model_dir, "*.bin")
    )
    if not weights:
        return False

    # Verify at least one weight file is non-empty
    for w in weights:
        if os.path.getsize(w) > 1024 * 1024:  # At least 1 MB
            return True

    return False


def main():
    model_id = os.environ.get("MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct")
    cache_root = os.environ.get("CACHE_DIR", "/data/models")
    
    # Sanitize folder name from model repo id
    safe_name = model_id.replace("/", "--")
    target_dir = os.environ.get("MODEL_DIR", os.path.join(cache_root, safe_name))
    hf_token = os.environ.get("HF_TOKEN", "").strip() or None

    print(f"==================================================")
    print(f"[Model Downloader] Target Model: {model_id}")
    print(f"[Model Downloader] Local Storage: {target_dir}")
    print(f"[Model Downloader] HF Token Configured: {'Yes' if hf_token else 'No'}")
    print(f"==================================================")

    if check_model_cached(target_dir):
        print(f"[Model Downloader] [CACHE HIT] Model is already downloaded at: {target_dir}")
        print(f"[Model Downloader] Skipping Hugging Face download.")
        sys.exit(0)

    print(f"[Model Downloader] [CACHE MISS] Model not found in local cache.")
    print(f"[Model Downloader] Downloading model weights from Hugging Face...")
    os.makedirs(target_dir, exist_ok=True)

    try:
        snapshot_download(
            repo_id=model_id,
            local_dir=target_dir,
            token=hf_token,
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        print(f"[Model Downloader] [SUCCESS] Model successfully downloaded and cached to: {target_dir}")
        sys.exit(0)
    except Exception as e:
        print(f"[Model Downloader] [ERROR] Failed to download model: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
