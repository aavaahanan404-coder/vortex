"""Synthetic phone photos of corpus scans, with known page corners (for tests and harness.eval_perspective).

photograph(page_bgr, seed) -> (photo, true_corners TL,TR,BR,BL in photo px). The page is placed on a textured background with a
random rotation (+-15 deg), perspective (corner jitter up to 7% of the page), uneven lighting, blur and noise. Deterministic per seed.
The corpus is never modified: pages are read, the photo is a new array.
"""
from __future__ import annotations

import cv2
import numpy as np


def background(h: int, w: int, rs: np.random.RandomState, kind: str) -> np.ndarray:
    base = {"dark": (45, 50, 55), "wood": (60, 95, 140), "light": (200, 205, 210)}[kind]
    low = cv2.resize(rs.randint(0, 255, (6, 8, 3), np.uint8), (w, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    noise = rs.normal(0, 8, (h, w, 3))
    bg = np.array(base, np.float32)[None, None, :] * 0.8 + (low - 128) * 0.12 + noise
    return np.clip(bg, 0, 255).astype(np.uint8)


def photograph(page_bgr: np.ndarray, seed: int = 0, canvas=(3000, 2250), kind: str | None = None, tilt: float | None = None,
               jitter: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    rs = np.random.RandomState(seed)
    H, W = canvas
    ph, pw = page_bgr.shape[:2]
    kind = kind or ["dark", "wood", "light"][seed % 3]
    scale = rs.uniform(0.55, 0.78) * min(W / pw, H / ph)
    ang = np.radians(rs.uniform(-15, 15) if tilt is None else tilt)
    jit = rs.uniform(0.02, 0.07) if jitter is None else jitter
    pts = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], np.float32)
    c = pts.mean(axis=0)
    rot = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]], np.float32)
    dst = ((pts - c) * scale) @ rot.T + np.array([W / 2 + rs.uniform(-30, 30), H / 2 + rs.uniform(-30, 30)], np.float32)
    dst += rs.uniform(-1, 1, (4, 2)).astype(np.float32) * jit * scale * np.array([pw, ph], np.float32)
    M = cv2.getPerspectiveTransform(pts, dst.astype(np.float32))
    photo = background(H, W, rs, kind)
    warped = cv2.warpPerspective(page_bgr, M, (W, H), flags=cv2.INTER_CUBIC, borderValue=(0, 0, 0))
    mask = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), M, (W, H), flags=cv2.INTER_LINEAR)
    shadow = cv2.GaussianBlur(mask, (0, 0), 25).astype(np.float32) / 255
    photo = (photo * (1 - 0.35 * shadow[..., None])).astype(np.uint8)                       # soft shadow under the page
    photo = np.where(mask[..., None] > 127, warped, photo)
    gx = np.linspace(rs.uniform(0.75, 1.0), rs.uniform(0.9, 1.1), W, dtype=np.float32)[None, :, None]   # uneven light
    photo = np.clip(photo.astype(np.float32) * gx + rs.normal(0, 3, photo.shape), 0, 255).astype(np.uint8)
    photo = cv2.GaussianBlur(photo, (0, 0), rs.uniform(0.6, 1.2))
    return photo, dst.astype(np.float32)
