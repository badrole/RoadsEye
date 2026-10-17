"""Rebuild notebooks/02_training.ipynb from scratch with all fixes."""
import json
import os

nb = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.10.0"},
        "colab": {"provenance": [], "gpuType": "T4"},
        "accelerator": "GPU",
    },
    "cells": [],
}


def md(cell_id, source):
    return {"cell_type": "markdown", "id": cell_id, "metadata": {}, "source": source}


def code(cell_id, source):
    return {
        "cell_type": "code",
        "id": cell_id,
        "metadata": {},
        "source": source,
        "outputs": [],
        "execution_count": None,
    }


cells = nb["cells"]

# ── Intro ────────────────────────────────────────────────────────────────────
cells.append(
    md(
        "markdown-intro",
        "# Phase 2 \u2014 YOLOv8s Fine-Tuning & ONNX Export\n"
        "\n"
        "**Run on Google Colab with T4 GPU** (Runtime \u2192 Change runtime type \u2192 T4 GPU)\n"
        "\n"
        "**Prerequisite:** `01_data_exploration.ipynb` must have been run \u2014 dataset saved to "
        "Drive at `TrafficVision/datasets/traffic_yolo/`\n"
        "\n"
        "Steps:\n"
        "1. Mount Drive & configure paths\n"
        "2. Install dependencies\n"
        "3. Verify GPU\n"
        "4. Copy dataset to Colab SSD (fast I/O via parallel zip)\n"
        "5. Fine-tune YOLOv8s (pretrained COCO \u2192 our 4-class traffic dataset)\n"
        "6. Plot training curves\n"
        "7. Evaluate \u2014 mAP, per-class metrics, confusion matrix, sample predictions\n"
        "8. Export to ONNX\n"
        "9. Verify ONNX inference speed\n"
        "10. Save weights to Drive\n"
        "\n"
        "### SSD copy strategy\n"
        "- **First run ever**: Reads dataset from Drive using 32 parallel threads (much faster "
        "than sequential FUSE reads). Creates `traffic_yolo.zip` on Drive. Takes ~5-20 min once.\n"
        "- **Every future session**: Copies the single zip file from Drive to SSD (~1-3 min) "
        "then extracts locally. No per-file FUSE latency.\n"
        "- **Training**: `cache=False` \u2014 SSD reads are ~500 MB/s so no RAM cache needed.",
    )
)

# ── 1. Mount Drive ──────────────────────────────────────────────────────────
cells.append(md("markdown-drive", "## 1. Mount Google Drive"))
cells.append(
    code(
        "cell-mount",
        'import os, sys\n'
        '\n'
        "IN_COLAB = 'google.colab' in sys.modules or 'COLAB_BACKEND_VERSION' in os.environ\n"
        '\n'
        'if IN_COLAB:\n'
        '    from google.colab import drive\n'
        "    drive.mount('/content/drive')\n"
        "    DRIVE_BASE = '/content/drive/MyDrive/TrafficVision'\n"
        'else:\n'
        "    _repo = os.path.abspath(os.path.join(os.getcwd(), '..'))\n"
        "    DRIVE_BASE = os.path.join(_repo, 'data', 'TrafficVision')\n"
        "    print('[LOCAL MODE] Using local data/ directory')\n"
        '\n'
        "DATASET_YAML = os.path.join(DRIVE_BASE, 'datasets', 'traffic_yolo', 'dataset.yaml')\n"
        "WEIGHTS_DIR  = os.path.join(DRIVE_BASE, 'weights')\n"
        "RUNS_DIR     = '/content/runs' if IN_COLAB else os.path.join("
        "_repo if not IN_COLAB else '/content', 'runs')\n"
        '\n'
        'os.makedirs(WEIGHTS_DIR, exist_ok=True)\n'
        'os.makedirs(RUNS_DIR, exist_ok=True)\n'
        '\n'
        'assert os.path.exists(DATASET_YAML), (\n'
        "    f'dataset.yaml not found at {DATASET_YAML}\\n'\n"
        "    'Run 01_data_exploration.ipynb first to prepare the dataset.'\n"
        ')\n'
        '\n'
        "print(f'Drive base   : {DRIVE_BASE}')\n"
        "print(f'Dataset yaml : {DATASET_YAML}')\n"
        "print(f'Weights dir  : {WEIGHTS_DIR}')\n"
        "print(f'Runs dir     : {RUNS_DIR}')",
    )
)

# ── 2. Install ───────────────────────────────────────────────────────────────
cells.append(md("markdown-install", "## 2. Install Dependencies"))
cells.append(code("cell-install", "!pip install -q ultralytics onnx onnxruntime-gpu"))

# ── 3. Verify GPU ───────────────────────────────────────────────────────────
cells.append(md("markdown-gpu", "## 3. Verify GPU"))
cells.append(
    code(
        "cell-gpu",
        'import torch\n'
        '\n'
        "print(f'PyTorch version : {torch.__version__}')\n"
        "print(f'CUDA available  : {torch.cuda.is_available()}')\n"
        'if torch.cuda.is_available():\n'
        '    props = torch.cuda.get_device_properties(0)\n'
        "    print(f'GPU             : {props.name}')\n"
        "    print(f'VRAM            : {props.total_memory / 1e9:.1f} GB')\n"
        'else:\n'
        "    print('WARNING: No GPU - enable GPU in Runtime > Change runtime type')\n"
        "    print('Training will be extremely slow on CPU.')",
    )
)

# ── 4. Copy dataset to SSD ──────────────────────────────────────────────────
cells.append(
    md(
        "markdown-copy-dataset",
        "## 4. Copy Dataset to Colab SSD\n"
        "\n"
        "**Why not train directly from Drive?**  \n"
        "Drive FUSE reads each small image file individually at ~0.2 MB/s with ~50ms per-file "
        "latency. 5800 images \u00d7 1.3 s/image = ~2 hours just for the RAM cache build every session.\n"
        "\n"
        "**The fix**: Copy the whole dataset as a single zip file to Colab's NVMe SSD, then "
        "train directly from SSD at 500 MB/s with `cache=False`.\n"
        "\n"
        "| Scenario | Time |\n"
        "|----------|------|\n"
        "| First run \u2014 parallel zip creation | ~5\u201320 min (once ever) |\n"
        "| Future sessions \u2014 copy zip + extract | ~1\u20133 min |\n"
        "| Training per epoch (SSD, cache=False) | ~3\u20135 min |",
    )
)

cells.append(
    code(
        "cell-copy-dataset",
        'import os, time, shutil, zipfile, threading, subprocess\n'
        'import yaml as _yaml\n'
        'from pathlib import Path\n'
        'from concurrent.futures import ThreadPoolExecutor, as_completed\n'
        '\n'
        "LOCAL_EXTRACT  = '/content/traffic_yolo_local'  # extraction target\n"
        "DRIVE_DATASETS = f'{DRIVE_BASE}/datasets'\n"
        "DRIVE_ZIP      = f'{DRIVE_DATASETS}/traffic_yolo.zip'\n"
        "LOCAL_ZIP      = '/content/traffic_yolo.zip'\n"
        "SRC_DIR        = f'{DRIVE_DATASETS}/traffic_yolo'\n"
        '\n'
        '\n'
        'def _find_dataset_root(base):\n'
        '    """Find the directory under base that contains images/."""\n'
        '    base = Path(base)\n'
        '    if (base / "images").exists():\n'
        '        return base\n'
        '    for sub in sorted(base.iterdir()):\n'
        '        if sub.is_dir() and (sub / "images").exists():\n'
        '            return sub\n'
        '    raise RuntimeError(\n'
        '        f"Cannot find images/ under {base}.\\n"\n'
        '        f"Contents: {[p.name for p in base.iterdir()]}"\n'
        '    )\n'
        '\n'
        '\n'
        'if IN_COLAB:\n'
        '    # Force Drive FUSE to populate directory listings before any exists() check.\n'
        '    # Without this, os.path.exists() can return False on files that are there.\n'
        '    subprocess.run(["ls", DRIVE_BASE],     capture_output=True)\n'
        '    subprocess.run(["ls", DRIVE_DATASETS], capture_output=True)\n'
        '    print(f"Drive datasets dir: {os.listdir(DRIVE_DATASETS) if os.path.isdir(DRIVE_DATASETS) else \"NOT FOUND\"}")\n'
        '\n'
        '    # ── Step 1: get dataset onto local SSD ───────────────────────────\n'
        '    if os.path.exists(LOCAL_EXTRACT):\n'
        "        print('Dataset already on SSD — skipping copy.')\n"
        '\n'
        '    elif os.path.exists(DRIVE_ZIP):\n'
        '        # Validate local zip — delete if missing, empty, or corrupt\n'
        '        _zip_ok = False\n'
        '        if os.path.exists(LOCAL_ZIP) and os.path.getsize(LOCAL_ZIP) > 0:\n'
        '            try:\n'
        '                import zipfile as _zfmod\n'
        '                with _zfmod.ZipFile(LOCAL_ZIP) as _ztest:\n'
        '                    _zip_ok = len(_ztest.namelist()) > 0\n'
        '            except Exception:\n'
        '                _zip_ok = False\n'
        '        if not _zip_ok:\n'
        '            if os.path.exists(LOCAL_ZIP):\n'
        '                print(f"Local zip invalid/corrupt — re-copying from Drive...")\n'
        '                os.remove(LOCAL_ZIP)\n'
        '            sz = os.path.getsize(DRIVE_ZIP) / 1e6\n'
        "            print(f'Copying zip from Drive ({sz:.0f} MB) — ~1-3 min...')\n"
        '            t0 = time.time()\n'
        '            shutil.copy2(DRIVE_ZIP, LOCAL_ZIP)\n'
        "            print(f'Copied in {time.time()-t0:.0f}s')\n"
        '        else:\n'
        "            print(f'Local zip valid — skipping Drive copy.')\n"
        "        print('Extracting to SSD...')\n"
        '        t0 = time.time()\n'
        "        subprocess.run(['unzip', '-q', LOCAL_ZIP, '-d', LOCAL_EXTRACT], check=True)\n"
        "        print(f'Extracted in {time.time()-t0:.0f}s')\n"
        '\n'
        '    else:\n'
        '        # One-time: parallel zip creation (32 threads overlap Drive FUSE latency)\n'
        "        print('One-time setup: archiving dataset with 32 parallel threads...')\n"
        "        print('(Happens ONCE — future sessions copy the zip in ~1-3 min)')\n"
        '        src   = Path(SRC_DIR)\n'
        '        files = sorted(f for f in src.rglob("*") if f.is_file())\n'
        "        print(f'Found {len(files)} files to archive...')\n"
        '        t0      = time.time()\n'
        '        lock    = threading.Lock()\n'
        '        counter = [0]\n'
        '\n'
        '        def _archive_file(f):\n'
        '            data    = f.read_bytes()\n'
        '            arcname = str(f.relative_to(src))\n'
        '            with lock:\n'
        '                _zf.writestr(arcname, data)\n'
        '                counter[0] += 1\n'
        '                if counter[0] % 1000 == 0:\n'
        "                    print(f'  {counter[0]}/{len(files)} files archived...')\n"
        '\n'
        '        with zipfile.ZipFile(LOCAL_ZIP, "w", zipfile.ZIP_STORED) as _zf:\n'
        '            with ThreadPoolExecutor(max_workers=32) as pool:\n'
        '                futures = [pool.submit(_archive_file, f) for f in files]\n'
        '                for fut in as_completed(futures):\n'
        '                    fut.result()\n'
        '\n'
        '        elapsed = time.time() - t0\n'
        '        size_mb = os.path.getsize(LOCAL_ZIP) / 1e6\n'
        "        print(f'Zip: {size_mb:.0f} MB in {elapsed:.0f}s ({elapsed/60:.1f} min)')\n"
        "        print('Saving zip to Drive for future sessions...')\n"
        '        shutil.copy2(LOCAL_ZIP, DRIVE_ZIP)\n'
        "        print('Extracting to SSD...')\n"
        "        subprocess.run(['unzip', '-q', LOCAL_ZIP, '-d', LOCAL_EXTRACT], check=True)\n"
        "        print('Done.')\n"
        '\n'
        '    # ── Step 2: find actual dataset root (handles zip subdirectory wrappers) ──\n'
        '    _root = _find_dataset_root(LOCAL_EXTRACT)\n'
        "    print(f'Dataset root: {_root}')\n"
        '\n'
        '    # ── Step 3: write dataset.yaml pointing at the correct local root ──\n'
        "    LOCAL_YAML = '/content/dataset.yaml'\n"
        '    _yaml_src = _root / "dataset.yaml"\n'
        '    if not _yaml_src.exists():\n'
        '        _yaml_src = Path(SRC_DIR) / "dataset.yaml"\n'
        '    with open(_yaml_src) as _f:\n'
        '        _cfg = _yaml.safe_load(_f)\n'
        '\n'
        '    _cfg["path"] = str(_root)  # absolute path to the correct root\n'
        '\n'
        '    # Verify/fix split paths against what actually exists on disk\n'
        '    _SPLIT_ALT = {\n'
        "        'train': ['images/train', 'train'],\n"
        "        'val':   ['images/val', 'images/valid', 'images/validation',\n"
        "                  'val', 'valid', 'validation', 'images/test', 'test'],\n"
        "        'test':  ['images/test', 'test', 'images/val', 'images/valid'],\n"
        '    }\n'
        "    for _split, _alts in _SPLIT_ALT.items():\n"
        '        _declared = _cfg.get(_split)\n'
        '        if not _declared:\n'
        '            continue\n'
        '        if (_root / _declared).exists():\n'
        '            print(f"  {_split}: {_declared!r} OK")\n'
        '        else:\n'
        '            for _alt in _alts:\n'
        '                if (_root / _alt).exists():\n'
        '                    print(f"  {_split}: {_declared!r} -> {_alt!r}")\n'
        '                    _cfg[_split] = _alt\n'
        '                    break\n'
        '            else:\n'
        '                print(f"  WARNING: no directory found for {_split!r} under {_root}")\n'
        '\n'
        '    # Final assertion — catch bad paths before training starts\n'
        '    for _split in ("train", "val"):\n'
        '        _p = _root / _cfg[_split]\n'
        '        assert _p.exists(), (\n'
        '            f"Split {_split!r} path does not exist: {_p}\\n"\n'
        '            f"Contents of {_root}: {[x.name for x in _root.iterdir()]}"\n'
        '        )\n'
        '\n'
        "    with open(LOCAL_YAML, 'w') as _f:\n"
        '        _yaml.dump(_cfg, _f, default_flow_style=False)\n'
        '    DATASET_YAML = LOCAL_YAML\n'
        "    print(f'dataset.yaml OK — path={_root}, train={_cfg[\"train\"]}, val={_cfg[\"val\"]}')\n"
        '\n'
        'else:\n'
        "    print('[LOCAL] Skipping dataset copy')",
    )
)

# ── 5. Fine-tune ────────────────────────────────────────────────────────────
cells.append(
    md(
        "markdown-train",
        "## 5. Fine-tune YOLOv8s\n"
        "\n"
        "Starting from `yolov8s.pt` (pretrained on COCO 80 classes) and fine-tuning on our "
        "4-class traffic dataset. The pretrained backbone gives us strong feature extraction "
        "out of the box; fine-tuning adapts the head to our specific classes.\n"
        "\n"
        "**`cache=False`**: Data is on local SSD (500 MB/s). No RAM cache needed \u2014 "
        "disk reads are ~instant compared to a GPU training step.\n"
        "\n"
        "**Augmentation:** `degrees=0` (fixed cameras), `flipud=0` (no upside-down vehicles), "
        "`mosaic=1.0` (small/occluded vehicles), `scale=0.5` (distance variation)\n"
        "\n"
        "**Checkpoints:** After every epoch, `last.pt` and `best.pt` are copied to Drive. "
        "If interrupted, the next run auto-resumes from Drive checkpoint.",
    )
)

cells.append(
    code(
        "cell-train",
        'import os, shutil, subprocess\n'
        'from ultralytics import YOLO\n'
        '\n'
        "MODEL_NAME = 'yolov8s_traffic'\n"
        "BEST_PT    = f'{RUNS_DIR}/{MODEL_NAME}/weights/best.pt'\n"
        "LAST_PT    = f'{RUNS_DIR}/{MODEL_NAME}/weights/last.pt'\n"
        "DRIVE_LAST = f'{WEIGHTS_DIR}/last.pt'\n"
        "DRIVE_BEST = f'{WEIGHTS_DIR}/best.pt'\n"
        '\n'
        "print(f'Training data : {DATASET_YAML}')\n"
        "assert '/content/' in DATASET_YAML or not IN_COLAB, (\n"
        "    'DATASET_YAML still points to Drive! Run cell-copy-dataset first.'\n"
        ')\n'
        '\n'
        '# Force Drive FUSE to sync before checking for checkpoints\n'
        'if IN_COLAB:\n'
        '    subprocess.run(["ls", WEIGHTS_DIR], capture_output=True)\n'
        "    print(f'Weights dir contents: {os.listdir(WEIGHTS_DIR) if os.path.isdir(WEIGHTS_DIR) else \"empty\"}')\n"
        '\n'
        'def save_to_drive(trainer):\n'
        '    if not IN_COLAB:\n'
        '        return\n'
        '    if os.path.exists(LAST_PT):\n'
        '        shutil.copy2(LAST_PT, DRIVE_LAST)\n'
        '    if os.path.exists(BEST_PT):\n'
        '        shutil.copy2(BEST_PT, DRIVE_BEST)\n'
        '    if trainer.epoch % 5 == 0:\n'
        "        print(f'[Drive] Checkpoints saved at epoch {trainer.epoch + 1}')\n"
        '\n'
        'if os.path.exists(DRIVE_LAST):\n'
        "    print(f'Resuming from {DRIVE_LAST}')\n"
        '    model = YOLO(DRIVE_LAST)\n'
        '    resume = True\n'
        'else:\n'
        "    print('Starting fresh from yolov8s.pt')\n"
        "    model = YOLO('yolov8s.pt')\n"
        '    resume = False\n'
        '\n'
        "model.add_callback('on_train_epoch_end', save_to_drive)\n"
        '\n'
        'results = model.train(\n'
        '    data=DATASET_YAML,\n'
        '    epochs=50,\n'
        '    imgsz=640,\n'
        '    batch=16,\n'
        '    cache=False,     # data is on local SSD — reads are instant, no RAM cache needed\n'
        '    device=0,\n'
        '    workers=4,\n'
        '    patience=15,\n'
        '    save=True,\n'
        '    save_period=10,\n'
        '    project=RUNS_DIR,\n'
        '    name=MODEL_NAME,\n'
        '    exist_ok=True,\n'
        '    resume=resume,\n'
        '    hsv_h=0.015,\n'
        '    hsv_s=0.7,\n'
        '    hsv_v=0.4,\n'
        '    degrees=0.0,\n'
        '    translate=0.1,\n'
        '    scale=0.5,\n'
        '    flipud=0.0,\n'
        '    fliplr=0.5,\n'
        '    mosaic=1.0,\n'
        '    mixup=0.1,\n'
        '    copy_paste=0.1,\n'
        '    plots=True,\n'
        '    verbose=True,\n'
        ')',
    )
)

# ── 6. Training Curves ──────────────────────────────────────────────────────
cells.append(md("markdown-curves", "## 6. Training Curves"))
cells.append(
    code(
        "cell-curves",
        'import pandas as pd\n'
        'import matplotlib.pyplot as plt\n'
        'import os\n'
        '\n'
        "results_csv = os.path.join(RUNS_DIR, MODEL_NAME, 'results.csv')\n"
        "assert os.path.exists(results_csv), f'results.csv not found at {results_csv}'\n"
        '\n'
        'df = pd.read_csv(results_csv)\n'
        'df.columns = df.columns.str.strip()\n'
        '\n'
        'fig, axes = plt.subplots(2, 3, figsize=(18, 10))\n'
        'axes = axes.flatten()\n'
        '\n'
        'metrics = [\n'
        "    ('train/box_loss',      'Train Box Loss',      '#e84c4c'),\n"
        "    ('train/cls_loss',      'Train Class Loss',     '#e8a44c'),\n"
        "    ('train/dfl_loss',      'Train DFL Loss',       '#8be84c'),\n"
        "    ('metrics/mAP50(B)',    'Val mAP@0.5',          '#4c8be8'),\n"
        "    ('metrics/mAP50-95(B)', 'Val mAP@0.5:0.95',    '#be84c8'),\n"
        "    ('val/box_loss',        'Val Box Loss',          '#4ce8e8'),\n"
        ']\n'
        '\n'
        'for ax, (col, title, color) in zip(axes, metrics):\n'
        '    if col in df.columns:\n'
        "        ax.plot(df['epoch'], df[col], color=color, linewidth=2)\n"
        '        ax.set_title(title, fontsize=12)\n'
        "        ax.set_xlabel('Epoch')\n"
        '        ax.grid(True, alpha=0.3)\n'
        "        best_val = df[col].min() if 'loss' in col else df[col].max()\n"
        "        best_ep  = df.loc[df[col].idxmax() if 'mAP' in col else df[col].idxmin(), 'epoch']\n"
        "        ax.axvline(best_ep, color='red', linestyle='--', alpha=0.5,\n"
        "                   label=f'Best: {best_val:.4f} @ ep{int(best_ep)}')\n"
        '        ax.legend(fontsize=9)\n'
        '    else:\n'
        "        ax.set_title(f'{title} (not found)', fontsize=10)\n"
        "        ax.axis('off')\n"
        '\n'
        "plt.suptitle('Training Curves - YOLOv8s Traffic', fontsize=15, fontweight='bold')\n"
        'plt.tight_layout()\n'
        "save_path = os.path.join(DRIVE_BASE, 'datasets', '05_training_curves.png')\n"
        "plt.savefig(save_path, dpi=120, bbox_inches='tight')\n"
        'plt.show()\n'
        "print('Saved:', save_path)",
    )
)

# ── 7. Evaluate ─────────────────────────────────────────────────────────────
cells.append(md("markdown-eval", "## 7. Evaluate on Validation Set"))
cells.append(
    code(
        "cell-eval",
        'import os\n'
        'from ultralytics import YOLO\n'
        '\n'
        "assert os.path.exists(BEST_PT), f'Best weights not found: {BEST_PT}'\n"
        '\n'
        'model   = YOLO(BEST_PT)\n'
        'metrics = model.val(\n'
        '    data=DATASET_YAML,\n'
        '    imgsz=640,\n'
        '    device=0,\n'
        '    plots=True,\n'
        '    save_json=False,\n'
        ')\n'
        '\n'
        "CLASS_NAMES = ['car', 'bus', 'motorcycle', 'truck']\n"
        '\n'
        'print()\n'
        "print('=' * 45)\n"
        "print(f'  Overall mAP@0.5     : {metrics.box.map50:.4f}')\n"
        "print(f'  Overall mAP@0.5:0.95: {metrics.box.map:.4f}')\n"
        "print(f'  Precision           : {metrics.box.mp:.4f}')\n"
        "print(f'  Recall              : {metrics.box.mr:.4f}')\n"
        "print('=' * 45)\n"
        "print('  Per-class AP@0.5:')\n"
        'for i, (name, ap) in enumerate(zip(CLASS_NAMES, metrics.box.ap50)):\n'
        "    print(f'    {name:<12}: {ap:.4f}')\n"
        "print('=' * 45)",
    )
)

# ── Per-class AP ─────────────────────────────────────────────────────────────
cells.append(md("markdown-perclass", "### Per-class AP"))
cells.append(
    code(
        "cell-perclass",
        'import matplotlib.pyplot as plt\n'
        '\n'
        "CLASS_NAMES  = ['car', 'bus', 'motorcycle', 'truck']\n"
        "CLASS_COLORS = ['#4c8be8', '#e8a44c', '#e84c4c', '#8be84c']\n"
        'ap50_vals = list(metrics.box.ap50)\n'
        '\n'
        'fig, ax = plt.subplots(figsize=(8, 5))\n'
        "bars = ax.bar(CLASS_NAMES, ap50_vals, color=CLASS_COLORS, edgecolor='none', width=0.5)\n"
        'ax.set_ylim(0, 1.05)\n'
        "ax.set_ylabel('AP @ IoU=0.50', fontsize=12)\n"
        "ax.set_title(f'Per-Class AP@0.5  |  mAP@0.5 = {metrics.box.map50:.4f}', fontsize=13)\n"
        "ax.axhline(metrics.box.map50, color='red', linestyle='--', linewidth=1.5,\n"
        "           label=f'mAP@0.5 = {metrics.box.map50:.4f}')\n"
        'ax.legend()\n'
        'for bar, val in zip(bars, ap50_vals):\n'
        '    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.01,\n'
        "            f'{val:.3f}', ha='center', fontsize=11, fontweight='bold')\n"
        'plt.tight_layout()\n'
        "save_path = os.path.join(DRIVE_BASE, 'datasets', '06_per_class_ap.png')\n"
        "plt.savefig(save_path, dpi=120, bbox_inches='tight')\n"
        'plt.show()\n'
        "print('Saved:', save_path)",
    )
)

# ── Sample Predictions ───────────────────────────────────────────────────────
cells.append(md("markdown-sample-preds", "### Sample Predictions"))
cells.append(
    code(
        "cell-sample-preds",
        'import cv2, random, yaml\n'
        'import matplotlib.pyplot as plt\n'
        'import matplotlib.patches as patches\n'
        'from pathlib import Path\n'
        '\n'
        'with open(DATASET_YAML) as f:\n'
        '    ds_cfg = yaml.safe_load(f)\n'
        '\n'
        "val_img_dir = Path(ds_cfg['path']) / ds_cfg['val']\n"
        "val_imgs    = sorted(val_img_dir.glob('*.jpg'))\n"
        'sample_imgs = random.sample(val_imgs, min(6, len(val_imgs)))\n'
        '\n'
        "CLASS_NAMES = ['car', 'bus', 'motorcycle', 'truck']\n"
        '\n'
        'fig, axes = plt.subplots(2, 3, figsize=(18, 10))\n'
        'axes = axes.flatten()\n'
        '\n'
        'for ax, img_path in zip(axes, sample_imgs):\n'
        '    result  = model(img_path, verbose=False, conf=0.3)[0]\n'
        '    img_rgb = cv2.cvtColor(cv2.imread(str(img_path)), cv2.COLOR_BGR2RGB)\n'
        '    ax.imshow(img_rgb)\n'
        '    for box in result.boxes:\n'
        '        cls_id = int(box.cls)\n'
        '        conf   = float(box.conf)\n'
        '        x1, y1, x2, y2 = box.xyxy[0].tolist()\n'
        "        color = ['#6464ff', '#ffb464', '#64ff64', '#ffc864'][cls_id % 4]\n"
        '        rect  = patches.Rectangle((x1, y1), x2 - x1, y2 - y1,\n'
        "                                   linewidth=1.5, edgecolor=color, facecolor='none')\n"
        '        ax.add_patch(rect)\n'
        "        ax.text(x1, y1 - 3, f'{CLASS_NAMES[cls_id]} {conf:.2f}',\n"
        "                color=color, fontsize=7, fontweight='bold')\n"
        '    ax.set_title(img_path.name, fontsize=8)\n'
        "    ax.axis('off')\n"
        '\n'
        "plt.suptitle('Sample Predictions on Validation Set (conf > 0.3)', fontsize=14, fontweight='bold')\n"
        'plt.tight_layout()\n'
        "save_path = os.path.join(DRIVE_BASE, 'datasets', '07_sample_predictions.png')\n"
        "plt.savefig(save_path, dpi=120, bbox_inches='tight')\n"
        'plt.show()\n'
        "print('Saved:', save_path)",
    )
)

# ── 8. ONNX Export ───────────────────────────────────────────────────────────
cells.append(
    md(
        "markdown-export",
        "## 8. Export to ONNX\n"
        "\n"
        "- `opset=17` \u2014 compatible with ONNX Runtime 1.16+\n"
        "- `simplify=True` \u2014 removes redundant ops\n"
        "- `dynamic=False` \u2014 fixed batch=1, better GPU optimization\n"
        "- `half=False` \u2014 FP32 for maximum compatibility",
    )
)

cells.append(
    code(
        "cell-export",
        'from ultralytics import YOLO\n'
        'import os\n'
        '\n'
        'model = YOLO(BEST_PT)\n'
        '\n'
        'export_path = model.export(\n'
        "    format='onnx',\n"
        '    imgsz=640,\n'
        '    opset=17,\n'
        '    simplify=True,\n'
        '    dynamic=False,\n'
        '    half=False,\n'
        ')\n'
        '\n'
        'ONNX_PATH = str(export_path)\n'
        "print(f'ONNX exported to: {ONNX_PATH}')\n"
        '\n'
        'size_mb = os.path.getsize(ONNX_PATH) / 1e6\n'
        "print(f'File size: {size_mb:.1f} MB')\n"
        "assert size_mb > 5, 'ONNX file suspiciously small - export may have failed'",
    )
)

# ── 9. Verify ONNX ──────────────────────────────────────────────────────────
cells.append(md("markdown-verify", "## 9. Verify ONNX Inference"))
cells.append(
    code(
        "cell-verify",
        'import onnxruntime as ort\n'
        'import numpy as np\n'
        'import time\n'
        '\n'
        "providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']\n"
        'sess      = ort.InferenceSession(ONNX_PATH, providers=providers)\n'
        '\n'
        "print(f'Providers active : {sess.get_providers()}')\n"
        "print(f'Input  name      : {sess.get_inputs()[0].name}')\n"
        "print(f'Input  shape     : {sess.get_inputs()[0].shape}')\n"
        "print(f'Output shape     : {sess.get_outputs()[0].shape}')\n"
        '\n'
        'dummy      = np.random.rand(1, 3, 640, 640).astype(np.float32)\n'
        "input_name = sess.get_inputs()[0].name\n"
        '\n'
        'for _ in range(3):\n'
        '    sess.run(None, {input_name: dummy})\n'
        '\n'
        'N = 100\n'
        't0 = time.perf_counter()\n'
        'for _ in range(N):\n'
        '    sess.run(None, {input_name: dummy})\n'
        'elapsed = time.perf_counter() - t0\n'
        '\n'
        "print(f'\\nBenchmark ({N} frames):')\n"
        "print(f'  Latency : {elapsed / N * 1000:.1f} ms/frame')\n"
        "print(f'  FPS     : {N / elapsed:.1f}')\n"
        "print(f'\\nONNX inference verified.')",
    )
)

# ── 10. Save to Drive ───────────────────────────────────────────────────────
cells.append(md("markdown-save", "## 10. Save Weights to Google Drive"))
cells.append(
    code(
        "cell-save",
        'import shutil\n'
        'from pathlib import Path\n'
        '\n'
        'src_pt   = Path(BEST_PT)\n'
        'src_onnx = Path(ONNX_PATH)\n'
        '\n'
        "dst_pt   = Path(WEIGHTS_DIR) / 'yolov8s_traffic_best.pt'\n"
        "dst_onnx = Path(WEIGHTS_DIR) / 'yolov8s_traffic.onnx'\n"
        '\n'
        'shutil.copy2(src_pt,   dst_pt)\n'
        'shutil.copy2(src_onnx, dst_onnx)\n'
        '\n'
        "print('Saved to Google Drive:')\n"
        "print(f'  PT   : {dst_pt}   ({dst_pt.stat().st_size / 1e6:.1f} MB)')\n"
        "print(f'  ONNX : {dst_onnx} ({dst_onnx.stat().st_size / 1e6:.1f} MB)')\n"
        '\n'
        'val_plots_dir = Path(RUNS_DIR) / MODEL_NAME\n'
        "for png in val_plots_dir.glob('*.png'):\n"
        "    dst = Path(DRIVE_BASE) / 'datasets' / png.name\n"
        '    shutil.copy2(png, dst)\n'
        "    print(f'  Plot : {dst.name}')\n"
        '\n'
        "print('\\nAll weights and plots saved to Drive.')",
    )
)

# ── Done ─────────────────────────────────────────────────────────────────────
cells.append(
    md(
        "markdown-done",
        "## Done \u2014 Phase 2 Complete\n"
        "\n"
        "### What was produced\n"
        "\n"
        "| File | Location on Drive |\n"
        "|------|-------------------|\n"
        "| Best PyTorch weights | `TrafficVision/weights/yolov8s_traffic_best.pt` |\n"
        "| ONNX model | `TrafficVision/weights/yolov8s_traffic.onnx` |\n"
        "| Training curves | `TrafficVision/datasets/05_training_curves.png` |\n"
        "| Per-class AP | `TrafficVision/datasets/06_per_class_ap.png` |\n"
        "| Sample predictions | `TrafficVision/datasets/07_sample_predictions.png` |\n"
        "| Confusion matrix | `TrafficVision/datasets/confusion_matrix.png` |\n"
        "\n"
        "### Next steps\n"
        "\n"
        "1. **Download the ONNX model** from Google Drive to your local machine:\n"
        "   ```\n"
        "   models/weights/yolov8s_traffic.onnx\n"
        "   ```\n"
        "2. **Start Phase 3** \u2014 build the core inference engine:\n"
        "   - `core/detector.py` \u2014 ONNX Runtime wrapper\n"
        "   - `core/tracker.py` \u2014 ByteTrack via supervision\n"
        "   - `core/analytics.py` \u2014 counting, speed, anomalies\n"
        "   - `core/pipeline.py` \u2014 orchestrator",
    )
)

# Write
out_path = os.path.join(os.path.dirname(__file__), '..', 'notebooks', '02_training.ipynb')
out_path = os.path.normpath(out_path)
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(nb, f, indent=1, ensure_ascii=True)

print(f"Written {len(cells)} cells to {out_path}")
