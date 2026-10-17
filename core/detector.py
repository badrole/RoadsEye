"""YOLOv8 ONNX Runtime inference wrapper.

Loads the exported ONNX model and runs inference on BGR numpy frames.
Uses CUDAExecutionProvider when available, falls back to CPU.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

try:
    import onnxruntime as ort
except ImportError as e:
    raise ImportError("onnxruntime-gpu is required: pip install onnxruntime-gpu") from e


CLASS_NAMES: list[str] = ["car", "bus", "motorcycle", "truck"]

# Default ONNX model path — override via MODEL_PATH env var
DEFAULT_MODEL_PATH = Path(__file__).parent.parent / "models" / "weights" / "yolov8s_traffic.onnx"


@dataclass
class Detection:
    """Single bounding box detection from one frame."""
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 (pixel coords)
    confidence: float
    class_id: int
    class_name: str = field(init=False)

    def __post_init__(self) -> None:
        self.class_name = CLASS_NAMES[self.class_id] if self.class_id < len(CLASS_NAMES) else "unknown"

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
        path = Path(
            model_path
            or os.environ.get("MODEL_PATH", "")
            or DEFAULT_MODEL_PATH
        )
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
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(str(path), sess_options=sess_opts, providers=providers)
        active = session.get_providers()
        using_gpu = "CUDAExecutionProvider" in active
        print(f"[Detector] Loaded {path.name} | {'GPU' if using_gpu else 'CPU'} | providers={active}")
        return session

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

        # BGR → RGB, HWC → CHW, [0,255] → [0,1]
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        blob = (rgb.transpose(2, 0, 1).astype(np.float32) / 255.0)[np.newaxis]
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
        preds = output[0].T  # (num_anchors, 8)

        pad_w, pad_h = pad
        detections: list[Detection] = []
        boxes_for_nms: list[list[float]] = []
        scores_for_nms: list[float] = []
        class_ids: list[int] = []

        for pred in preds:
            cx, cy, w, h = pred[:4]
            class_scores = pred[4:]
            class_id = int(np.argmax(class_scores))
            confidence = float(class_scores[class_id])

            if confidence < self.confidence_threshold:
                continue

            # Convert from padded/scaled coords back to original frame coords
            x1 = (cx - w / 2 - pad_w) / scale
            y1 = (cy - h / 2 - pad_h) / scale
            x2 = (cx + w / 2 - pad_w) / scale
            y2 = (cy + h / 2 - pad_h) / scale

            # Clamp to frame bounds
            x1 = max(0.0, min(float(orig_w), x1))
            y1 = max(0.0, min(float(orig_h), y1))
            x2 = max(0.0, min(float(orig_w), x2))
            y2 = max(0.0, min(float(orig_h), y2))

            boxes_for_nms.append([x1, y1, x2 - x1, y2 - y1])  # xywh for cv2.dnn.NMSBoxes
            scores_for_nms.append(confidence)
            class_ids.append(class_id)

        if not boxes_for_nms:
            return []

        # NMS
        indices = cv2.dnn.NMSBoxes(
            boxes_for_nms, scores_for_nms, self.confidence_threshold, self.iou_threshold
        )
        if indices is None or len(indices) == 0:
            return []

        for i in indices.flatten():
            x1, y1, w, h = boxes_for_nms[i]
            detections.append(
                Detection(
                    bbox=(x1, y1, x1 + w, y1 + h),
                    confidence=scores_for_nms[i],
                    class_id=class_ids[i],
                )
            )

        return detections

    def __repr__(self) -> str:
        return (
            f"Detector(model={self.model_path.name}, "
            f"conf={self.confidence_threshold}, iou={self.iou_threshold})"
        )
