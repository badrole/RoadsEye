"""YOLOv8 ONNX Runtime inference wrapper.

Loads the exported ONNX model and runs inference on BGR numpy frames.
Uses CUDAExecutionProvider when available, falls back to CPU.

Supports both the 4-class traffic model (output shape (1, 8, N)) and the
80-class COCO model (output shape (1, 84, N)). For COCO models only the
traffic classes are considered and mapped onto the internal class ids.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

try:
    import onnxruntime as ort
except ImportError as e:
    raise ImportError("onnxruntime-gpu is required: pip install onnxruntime-gpu") from e


# COCO to traffic class mapping
COCO_TRAFFIC_CLASSES: dict[int, int] = {
    2: 0,  # car -> 0
    5: 1,  # bus -> 1
    3: 2,  # motorcycle -> 2
    7: 3,  # truck -> 3
}

CLASS_NAMES: list[str] = ["car", "bus", "motorcycle", "truck"]

_COCO_IDS = np.array(list(COCO_TRAFFIC_CLASSES.keys()))
_TRAFFIC_IDS = np.array(list(COCO_TRAFFIC_CLASSES.values()))

# Default ONNX model path — override via MODEL_PATH env var
_ROOT_DIR = Path(__file__).parent.parent
DEFAULT_MODEL_PATH = _ROOT_DIR / "weights" / "yolov8s_traffic.onnx"
LEGACY_MODEL_PATH = _ROOT_DIR / "models" / "weights" / "yolov8s_traffic.onnx"


@dataclass
class Detection:
    """Single bounding box detection from one frame."""
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 (pixel coords)
    confidence: float
    class_id: int
    class_name: str = field(init=False)

    def __post_init__(self) -> None:
        self.class_name = CLASS_NAMES[self.class_id] if 0 <= self.class_id < len(CLASS_NAMES) else "vehicle"

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return self.bbox

    @property
    def xywh(self) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = self.bbox
        return x1, y1, x2 - x1, y2 - y1

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


class Detector:
    """ONNX Runtime inference wrapper for YOLOv8.

    Usage:
        detector = Detector()                     # loads from env or default path
        detector = Detector("models/weights/x.onnx")
        detections = detector.detect(bgr_frame)
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        confidence_threshold: float = 0.35,
        iou_threshold: float = 0.45,
        input_size: int = 640,
    ) -> None:
        raw_path = model_path or os.environ.get("MODEL_PATH", "")
        path = Path(raw_path) if raw_path else DEFAULT_MODEL_PATH
        if not path.exists():
            if DEFAULT_MODEL_PATH.exists():
                path = DEFAULT_MODEL_PATH
            elif LEGACY_MODEL_PATH.exists():
                path = LEGACY_MODEL_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"ONNX model not found: {path}\n"
                "Run Phase 2 notebook to export the model, then copy it to models/weights/."
            )

        self.model_path = path
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.input_size = input_size

        self._session = self._load_session(path)
        self._input_name: str = self._session.get_inputs()[0].name

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _load_session(self, path: Path) -> ort.InferenceSession:
        providers = [
            ("CUDAExecutionProvider", {"device_id": 0, "cudnn_conv_algo_search": "HEURISTIC"}),
            "CPUExecutionProvider",
        ]
        self._register_cuda_dll_dirs()
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(str(path), sess_options=sess_opts, providers=providers)
        active = session.get_providers()
        using_gpu = "CUDAExecutionProvider" in active
        print(f"[Detector] Loaded {path.name} | {'GPU' if using_gpu else 'CPU'} | providers={active}")
        if not using_gpu:
            print("[Detector] HINT: RTX 3050 tidak terpakai (fallback CPU). "
                  "Cek: pip wheel nvidia-*-cu12 terpasang & driver NVIDIA versi 566+.")
        return session

    @staticmethod
    def _register_cuda_dll_dirs() -> None:
        """Windows: point the DLL loader at CUDA/cuDNN wheels installed via pip.

        onnxruntime-gpu does not bundle CUDA; the nvidia-*-cu12 pip wheels carry
        the DLLs, but Windows only finds them if their dirs are registered.
        """
        if sys.platform != "win32":
            return
        site_pkgs = Path(ort.__file__).parent.parent
        for d in site_pkgs.glob("nvidia/*/bin"):
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass

    # ── Inference ─────────────────────────────────────────────────────────────

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run detection on a single BGR frame. Returns list of Detection objects."""
        orig_h, orig_w = frame.shape[:2]
        blob, scale, pad = self._preprocess(frame)
        raw_output = self._session.run(None, {self._input_name: blob})[0]
        detections = self._postprocess(raw_output, orig_w, orig_h, scale, pad)
        return detections

    # ── Pre/post processing ───────────────────────────────────────────────────

    def _preprocess(
        self, frame: np.ndarray
    ) -> tuple[np.ndarray, float, tuple[int, int]]:
        """Letterbox resize → CHW float32 blob. Returns (blob, scale, (pad_w, pad_h))."""
        s = self.input_size
        h, w = frame.shape[:2]

        # Scale keeping aspect ratio
        scale = min(s / w, s / h)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))

        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Pad to square
        pad_w = (s - new_w) // 2
        pad_h = (s - new_h) // 2
        padded = cv2.copyMakeBorder(
            resized, pad_h, s - new_h - pad_h, pad_w, s - new_w - pad_w,
            cv2.BORDER_CONSTANT, value=(114, 114, 114),
        )

        # BGR → RGB, HWC → NCHW, [0,255] → [0,1] in a single native pass
        blob = cv2.dnn.blobFromImage(padded, 1.0 / 255.0, swapRB=True)
        return blob, scale, (pad_w, pad_h)

    def _postprocess(
        self,
        output: np.ndarray,
        orig_w: int,
        orig_h: int,
        scale: float,
        pad: tuple[int, int],
    ) -> list[Detection]:
        """Parse YOLOv8 output tensor → Detection list with NMS applied.

        YOLOv8 ONNX output shape: (1, 8, num_anchors)
        where 8 = [cx, cy, w, h, cls0_score, cls1_score, cls2_score, cls3_score]
        """
        # output: (1, num_attrs, num_anchors) → transpose to (num_anchors, num_attrs)
        preds = output[0].T
        cls_scores = preds[:, 4:]
        rows = np.arange(len(preds))

        if cls_scores.shape[1] >= 80:
            # Best traffic class per anchor. Specificity gate: the anchor's overall
            # argmax across all 80 COCO classes must itself be a traffic class, so
            # a "person" that also scores on a vehicle column is rejected.
            sub = cls_scores[:, _COCO_IDS]
            best_col = sub.argmax(axis=1)
            conf = sub[rows, best_col]
            conf = np.where(np.isin(cls_scores.argmax(axis=1), _COCO_IDS), conf, 0.0)
            cls = _TRAFFIC_IDS[best_col]
        else:
            cls = cls_scores.argmax(axis=1)
            conf = cls_scores[rows, cls]

        keep = conf >= self.confidence_threshold
        if not keep.any():
            return []
        p, conf, cls = preds[keep], conf[keep], cls[keep]

        pad_w, pad_h = pad
        half_w, half_h = p[:, 2] / 2, p[:, 3] / 2
        x1 = np.clip((p[:, 0] - half_w - pad_w) / scale, 0, orig_w)
        y1 = np.clip((p[:, 1] - half_h - pad_h) / scale, 0, orig_h)
        x2 = np.clip((p[:, 0] + half_w - pad_w) / scale, 0, orig_w)
        y2 = np.clip((p[:, 1] + half_h - pad_h) / scale, 0, orig_h)
        boxes = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1)  # xywh for NMSBoxes

        indices = cv2.dnn.NMSBoxes(
            boxes.tolist(), conf.tolist(), self.confidence_threshold, self.iou_threshold
        )
        if indices is None or len(indices) == 0:
            return []

        initial_dets = [
            Detection(
                bbox=(float(x1[i]), float(y1[i]), float(x2[i]), float(y2[i])),
                confidence=float(conf[i]),
                class_id=int(cls[i]),
            )
            for i in np.asarray(indices).flatten()
        ]
        return self._merge_articulated_parts(initial_dets)

    @staticmethod
    def _merge_articulated_parts(detections: list[Detection]) -> list[Detection]:
        """Merge articulated vehicle parts (e.g. truck cabin + trailer/container).

        When a long container truck is detected as two separate boxes (cabin and
        trailer), standard NMS fails to suppress one because the IoU is low (~0.1-0.2).
        However, the intersection over smaller area (IoS) is high (>=0.35).
        This function merges overlapping parts into a single unified truck bounding box.
        """
        if len(detections) <= 1:
            return detections

        merged = []
        skip = set()

        for i in range(len(detections)):
            if i in skip:
                continue
            d1 = detections[i]
            bx1, by1, bx2, by2 = d1.bbox
            b_conf = d1.confidence
            b_cls = d1.class_id
            b_area = max(1.0, (bx2 - bx1) * (by2 - by1))

            for j in range(i + 1, len(detections)):
                if j in skip:
                    continue
                d2 = detections[j]

                # Never merge motorcycles (class 2)
                if d1.class_id == 2 or d2.class_id == 2:
                    continue

                # At least one must be a heavy vehicle (truck=3 or bus=1)
                if d1.class_id not in (1, 3) and d2.class_id not in (1, 3):
                    continue

                ox1, oy1, ox2, oy2 = d2.bbox
                o_area = max(1.0, (ox2 - ox1) * (oy2 - oy1))

                # Compute intersection
                ix1, iy1 = max(bx1, ox1), max(by1, oy1)
                ix2, iy2 = min(bx2, ox2), min(by2, oy2)
                iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
                inter = iw * ih

                min_area = min(b_area, o_area)
                ios = inter / min_area if min_area > 0 else 0.0

                # High overlap with the smaller sub-box (cabin or container)
                if ios >= 0.35:
                    skip.add(j)
                    # Union bounding box covers both cabin and container
                    bx1 = min(bx1, ox1)
                    by1 = min(by1, oy1)
                    bx2 = max(bx2, ox2)
                    by2 = max(by2, oy2)
                    b_area = max(1.0, (bx2 - bx1) * (by2 - by1))
                    b_conf = max(b_conf, d2.confidence)
                    if d2.class_id in (1, 3):
                        b_cls = d2.class_id

            merged.append(
                Detection(
                    bbox=(bx1, by1, bx2, by2),
                    confidence=b_conf,
                    class_id=b_cls,
                )
            )

        return merged

    def __repr__(self) -> str:
        return (
            f"Detector(model={self.model_path.name}, "
            f"conf={self.confidence_threshold}, iou={self.iou_threshold})"
        )
