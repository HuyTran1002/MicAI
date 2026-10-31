import ctypes
import os
import sys
import platform
from typing import List, Tuple, Union
import numpy as np

def get_librnnoise_path():
    # Khi đóng gói OneFile bằng PyInstaller
    if hasattr(sys, '_MEIPASS'):
        bundle_path = os.path.join(sys._MEIPASS, "rnnoise.dll")
        if os.path.exists(bundle_path):
            return bundle_path
        bundle_sub = os.path.join(sys._MEIPASS, "pyrnnoise", "rnnoise.dll")
        if os.path.exists(bundle_sub):
            return bundle_sub

    # Khi chạy trong môi trường code thường
    base_dir = os.path.dirname(__file__)
    local_dll = os.path.join(base_dir, "rnnoise.dll")
    if os.path.exists(local_dll):
        return local_dll
        
    venv_dll = os.path.join(base_dir, "venv", "Lib", "site-packages", "pyrnnoise", "rnnoise.dll")
    if os.path.exists(venv_dll):
        return venv_dll
        
    raise OSError(f"Không tìm thấy file rnnoise.dll ở {bundle_path if hasattr(sys, '_MEIPASS') else local_dll}")

LIBRNNOISE = get_librnnoise_path()
lib = ctypes.CDLL(LIBRNNOISE)

lib.rnnoise_create.argtypes = [ctypes.c_void_p]
lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
lib.rnnoise_process_frame.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_float),
    ctypes.POINTER(ctypes.c_float),
]
lib.rnnoise_create.restype = ctypes.c_void_p
lib.rnnoise_get_frame_size.restype = ctypes.c_int
lib.rnnoise_process_frame.restype = ctypes.c_float

FRAME_SIZE = lib.rnnoise_get_frame_size()
SAMPLE_RATE = 48000
FRAME_SIZE_MS = FRAME_SIZE * 1000 // SAMPLE_RATE
DTYPE = np.int16

def create() -> ctypes.c_void_p:
    """Tạo RNNoise denoise state mới"""
    return lib.rnnoise_create(None)

def destroy(state: ctypes.c_void_p):
    """Giải phóng RNNoise denoise state"""
    lib.rnnoise_destroy(state)

def process_mono_frame_float(state: ctypes.c_void_p, frame: np.ndarray) -> Tuple[np.ndarray, float]:
    """Xử lý lọc âm 1 frame (480 mẫu) float32 qua mô hình RNNoise C thuần túy mà không chuyển đổi qua int16"""
    frame_size = len(frame)
    scaled = (frame * 32767.0).astype(np.float32, copy=True)
    if frame_size < FRAME_SIZE:
        scaled = np.pad(scaled, (0, FRAME_SIZE - frame_size))
        
    ptr = scaled.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    speech_prob = float(lib.rnnoise_process_frame(state, ptr, ptr))
    out_frame = (scaled[:frame_size] * (1.0 / 32767.0)).astype(np.float32)
    return out_frame, speech_prob

def process_mono_frame(state: ctypes.c_void_p, frame: np.ndarray) -> Tuple[np.ndarray, ctypes.c_float]:
    """Xử lý lọc âm 1 frame (480 mẫu) qua mô hình RNNoise C thuần túy (tương thích ngược)"""
    if frame.dtype in (np.float32, np.float64) and frame.min() >= -1.0 and frame.max() <= 1.0:
        frame = (frame * 32767).astype(DTYPE)
    assert frame.dtype == DTYPE
    frame = frame.astype(np.float32)

    frame_size = len(frame)
    if frame_size < FRAME_SIZE:
        frame = np.pad(frame, (0, FRAME_SIZE - frame_size))
        
    ptr = frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    speech_prob = lib.rnnoise_process_frame(state, ptr, ptr)
    return frame.astype(DTYPE)[:frame_size], speech_prob

