import threading
import time
from collections import deque
import numpy as np
import sounddevice as sd
from rnnoise_native import create, process_mono_frame, process_mono_frame_float, destroy, FRAME_SIZE, SAMPLE_RATE

import ctypes
from ctypes import wintypes, HRESULT, POINTER, c_void_p, c_wchar_p, byref, c_ushort, Structure

try:
    ole32 = ctypes.windll.ole32

    class GUID(Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8)
        ]
        def __init__(self, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8):
            self.Data1 = l
            self.Data2 = w1
            self.Data3 = w2
            self.Data4 = (wintypes.BYTE * 8)(b1, b2, b3, b4, b5, b6, b7, b8)

    class PROPERTYKEY(Structure):
        _fields_ = [
            ("fmtid", GUID),
            ("pid", wintypes.DWORD)
        ]

    class PROPVARIANT(Structure):
        _fields_ = [
            ("vt", c_ushort),
            ("wReserved1", wintypes.WORD),
            ("wReserved2", wintypes.WORD),
            ("wReserved3", wintypes.WORD),
            ("pwszVal", c_wchar_p)
        ]

    CLSID_MMDeviceEnumerator = GUID(0xBCDE0395, 0xE52F, 0x467C, 0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E)
    IID_IMMDeviceEnumerator = GUID(0xA95664D2, 0x9614, 0x4F35, 0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6)
    PKEY_Device_FriendlyName = PROPERTYKEY(
        GUID(0xa45c254e, 0xdf1c, 0x4efd, 0x80, 0x20, 0x67, 0xd1, 0x46, 0xa8, 0x50, 0xe0), 14
    )

    def get_windows_default_playback():
        """Lấy real-time endpoint ID và tên thiết bị phát mặc định của Windows từ CoreAudio COM API"""
        try:
            ole32.CoInitialize(None)
        except Exception:
            pass
        pEnum = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(CLSID_MMDeviceEnumerator), None, 1,
            byref(IID_IMMDeviceEnumerator), byref(pEnum)
        )
        if hr != 0 or not pEnum.value:
            return None, None
            
        try:
            vtable = ctypes.cast(ctypes.cast(pEnum, POINTER(c_void_p))[0], POINTER(c_void_p))
            GetDefaultAudioEndpoint = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, wintypes.DWORD, POINTER(c_void_p))(vtable[4])
            
            pDevice = c_void_p()
            hr = GetDefaultAudioEndpoint(pEnum, 0, 0, byref(pDevice)) # eRender=0, eConsole=0
            if hr != 0 or not pDevice.value:
                return None, None
                
            try:
                dev_vtable = ctypes.cast(ctypes.cast(pDevice, POINTER(c_void_p))[0], POINTER(c_void_p))
                GetId = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_wchar_p))(dev_vtable[5])
                pStrId = c_wchar_p()
                GetId(pDevice, byref(pStrId))
                endpoint_id = pStrId.value
                
                OpenPropertyStore = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, POINTER(c_void_p))(dev_vtable[4])
                pStore = c_void_p()
                hr = OpenPropertyStore(pDevice, 0, byref(pStore))
                friendly_name = None
                if hr == 0 and pStore.value:
                    try:
                        store_vtable = ctypes.cast(ctypes.cast(pStore, POINTER(c_void_p))[0], POINTER(c_void_p))
                        GetValue = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))(store_vtable[5])
                        pv = PROPVARIANT()
                        hr = GetValue(pStore, byref(PKEY_Device_FriendlyName), byref(pv))
                        if hr == 0 and pv.vt == 31:
                            friendly_name = pv.pwszVal
                    finally:
                        Release_store = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(store_vtable[2])
                        Release_store(pStore)
                        
                return endpoint_id, friendly_name
            finally:
                Release_dev = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(dev_vtable[2])
                Release_dev(pDevice)
        finally:
            Release_enum = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtable[2])
            Release_enum(pEnum)

    def get_audio_endpoints_fingerprint():
        """Lấy danh sách ID các endpoint Render đang Active từ Windows CoreAudio COM để phát hiện cắm/rút tai nghe thời gian thực"""
        try:
            ole32.CoInitialize(None)
        except Exception:
            pass
        pEnum = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(CLSID_MMDeviceEnumerator), None, 1,
            byref(IID_IMMDeviceEnumerator), byref(pEnum)
        )
        if hr != 0 or not pEnum.value:
            return ()
        try:
            vtable = ctypes.cast(ctypes.cast(pEnum, POINTER(c_void_p))[0], POINTER(c_void_p))
            EnumAudioEndpoints = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, wintypes.DWORD, POINTER(c_void_p))(vtable[3])
            pCol = c_void_p()
            hr = EnumAudioEndpoints(pEnum, 0, 1, byref(pCol)) # eRender=0, DEVICE_STATE_ACTIVE=1
            if hr != 0 or not pCol.value:
                return ()
            try:
                col_vtable = ctypes.cast(ctypes.cast(pCol, POINTER(c_void_p))[0], POINTER(c_void_p))
                GetCount = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(wintypes.UINT))(col_vtable[3])
                Item = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.UINT, POINTER(c_void_p))(col_vtable[4])
                count = wintypes.UINT()
                GetCount(pCol, byref(count))
                ids = []
                for i in range(count.value):
                    pDev = c_void_p()
                    if Item(pCol, i, byref(pDev)) == 0 and pDev.value:
                        try:
                            dev_vtable = ctypes.cast(ctypes.cast(pDev, POINTER(c_void_p))[0], POINTER(c_void_p))
                            GetId = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_wchar_p))(dev_vtable[5])
                            pId = c_wchar_p()
                            if GetId(pDev, byref(pId)) == 0 and pId.value:
                                ids.append(pId.value)
                        finally:
                            Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(dev_vtable[2])
                            Release(pDev)
                return tuple(sorted(ids))
            finally:
                Release_col = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(col_vtable[2])
                Release_col(pCol)
        finally:
            Release_enum = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtable[2])
            Release_enum(pEnum)
except Exception:
    def get_windows_default_playback():
        return None, None
    def get_audio_endpoints_fingerprint():
        return ()

def find_sd_output_device_by_name(name, preferred_hostapi=2):
    """Tìm sounddevice ID theo tên thiết bị, ưu tiên preferred_hostapi (WASAPI = 2).
    Nếu không tìm thấy ở lần đầu (ví dụ tai nghe Bluetooth vừa kết nối), tự động refresh cache sounddevice."""
    if not name:
        return None
    name_clean = name.strip().lower()
    for attempt in range(2):
        try:
            devices = sd.query_devices()
            # Thử tìm cùng hostapi được ưu tiên
            if preferred_hostapi is not None:
                for idx, d in enumerate(devices):
                    if d['max_output_channels'] > 0 and d['hostapi'] == preferred_hostapi:
                        d_name = d['name'].strip().lower()
                        if d_name == name_clean or d_name in name_clean or name_clean in d_name:
                            return idx
            # Tìm trên bất kỳ hostapi nào
            for idx, d in enumerate(devices):
                if d['max_output_channels'] > 0:
                    d_name = d['name'].strip().lower()
                    if d_name == name_clean or d_name in name_clean or name_clean in d_name:
                        return idx
        except Exception:
            pass
        if attempt == 0:
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
    return None

class AudioRingBuffer:
    """Bộ đệm vòng (Ring Buffer) dùng NumPy mảng liên tục bộ nhớ C,
    thay thế hoàn toàn Python deque và for-loop để đạt 0% CPU, độ trễ tối ưu,
    không sinh rác (zero-garbage-collection), chống giật lag và gián đoạn âm thanh.
    """
    def __init__(self, capacity=19200, channels=2):
        self.capacity = capacity
        self.channels = channels
        if channels == 1:
            self.buffer = np.zeros(capacity, dtype=np.float32)
        else:
            self.buffer = np.zeros((capacity, channels), dtype=np.float32)
        self.write_idx = 0
        self.read_idx = 0
        self.available = 0
        self.lock = threading.Lock()

    def write(self, data: np.ndarray):
        with self.lock:
            n = len(data)
            if n == 0:
                return
            if n > self.capacity:
                data = data[-self.capacity:]
                n = self.capacity
            
            space = self.capacity - self.write_idx
            if n <= space:
                self.buffer[self.write_idx:self.write_idx + n] = data
            else:
                self.buffer[self.write_idx:] = data[:space]
                self.buffer[:n - space] = data[space:]
            self.write_idx = (self.write_idx + n) % self.capacity
            self.available += n
            
            # Bù trừ trôi nhịp đồng hồ phần cứng (Clock Drift) khi chạy 24/7 liên tục:
            # Giữ buffer tối đa ~80ms (3840 mẫu) để chống drop frame và triệt tiêu tiếng rè lách tách
            max_drift_threshold = 3840
            if self.available > max_drift_threshold:
                excess = self.available - max_drift_threshold
                self.read_idx = (self.read_idx + excess) % self.capacity
                self.available = max_drift_threshold

    def read(self, out: np.ndarray, num_frames: int) -> int:
        with self.lock:
            to_read = min(num_frames, self.available)
            if to_read == 0:
                out.fill(0)
                return 0
            
            space = self.capacity - self.read_idx
            if to_read <= space:
                out[:to_read] = self.buffer[self.read_idx:self.read_idx + to_read]
            else:
                out[:space] = self.buffer[self.read_idx:]
                out[space:to_read] = self.buffer[:to_read - space]
            if to_read < num_frames:
                out[to_read:].fill(0)
            self.read_idx = (self.read_idx + to_read) % self.capacity
            self.available -= to_read
            return to_read

    def clear(self):
        with self.lock:
            self.buffer.fill(0)
            self.write_idx = 0
            self.read_idx = 0
            self.available = 0

class AudioEngine:
    def __init__(self):
        self.sample_rate = SAMPLE_RATE  # 48000 Hz
        self.frame_size = FRAME_SIZE    # 480 samples = 10ms
        self.is_running = False
        
        # Cấu hình lọc âm và Proximity Noise Gate theo cự ly
        self.denoise_enabled = True
        self.noise_gate_threshold = 0.008  # Ngưỡng âm lượng tối thiểu (RMS) chuẩn cho mic gần
        self.vad_threshold = 0.28          # Ngưỡng tin cậy giọng nói AI (0.0 -> 1.0)
        self.gain = 1.0                    # Tăng/giảm âm lượng đầu ra (1.0 = 100%)
        
        # Trạng thái Noise Gate Envelope mượt mà (chống bụp rè, ngắt sạch tiếng ồn & TikTok xa)
        self.gate_gain = 0.0               # Hệ số mở mic mượt mà [0.0 = im lặng, 1.0 = thông giọng]
        self.gate_hold_counter = 0         # Bộ đếm giữ mic mở sau câu nói
        self.gate_hold_frames = 60         # 60 frames * 10ms = 600ms hold (giữ nhịp thở, không ngắt cụt câu)
        
        # Bộ lọc Infrasonic / Sub-bass 40Hz cắt sạch tiếng rung bàn, tiếng gió mic mà giữ trọn 100% âm sắc tự nhiên của giọng nói
        self.hp_alpha = float(1.0 / (1.0 + 2.0 * np.pi * 40.0 / self.sample_rate))
        self.hp_px = 0.0
        self.hp_py = 0.0
        
        # Loopback / Monitor (nghe lại giọng qua tai nghe/loa)
        self.monitor_enabled = False
        self.monitor_device_id = None
        self.monitor_stream = None
        
        # RNNoise C pointer
        self.rnnoise_state = None
        
        # Audio Devices
        self.input_device_id = None
        self.output_device_id = None
        self.headphone_device_id = None  # Tai nghe / Loa thật của bạn để nghe nhạc
        
        # Music Pass-Through / Background Audio Mixer
        self.music_enabled = False
        self.music_volume = 0.5          # Âm lượng nhạc nền (0.0 -> 1.0)
        self.music_audio_data = None     # Mảng numpy chứa bài nhạc đã load
        self.music_pos = 0               # Vị trí con trỏ phát nhạc
        self.music_file_name = ""
        self.headphone_stream = None     # Stream phát nhạc ra tai nghe của bạn
        
        # Web / Youtube / Browser Pass-Through (2 Kênh A & B Chống Vọng Tuyệt Đối)
        self.web_passthrough_enabled = False
        self.web_cable_stream = None     # Stream đọc từ Cáp A và phát vào tai nghe của bạn
        self.web_input_stream = None     # Fallback stream thu âm từ Cáp A
        self.web_output_stream = None    # Fallback stream phát ra tai nghe
        self.headphone_music_enabled = True # Bật/tắt nghe nhạc ở tai nghe của bạn (độc lập với Discord)
        self.headphone_music_volume = 0.5 # Âm lượng nhạc phát ra tai bạn
        self.web_to_mic_volume = 0.5     # Âm lượng nhạc trộn vào mic cho bạn bè nghe
        self.web_music_to_mic = False    # Mặc định KHÔNG tự động trộn vào mic Discord/Game (chỉ bật khi người dùng chủ động tích)
        
        # Bộ đệm vòng Ring Buffer hiệu năng cao, C-array memory contiguous, 0% CPU, zero-allocations
        self.web_music_buffer = AudioRingBuffer(capacity=9600, channels=1)
        self.web_hp_buffer = AudioRingBuffer(capacity=9600, channels=2)
        self.web_music_fifo = deque()
        self.web_music_lock = threading.Lock()
        self.web_hp_fifo = deque()
        self.web_hp_lock = threading.Lock()
        self.web_in_dev_id = None
        self.web_out_dev_id = None
        self.web_req_out_dev_id = None
        
        # Auto-Ducking / Voice Priority (Ưu tiên giọng nói, tự hạ nhạc khi bạn nói)
        self.auto_ducking_enabled = False # Mặc định tắt để âm thanh nhạc luôn thông suốt, không bị bóp nghẽn
        self.ducking_factor = 0.50        # Khi nói thật sự rõ ràng, nhạc chỉ giảm 50%
        self.current_ducking_gain = 1.0   # Mức âm lượng ducking hiện tại (smooth interpolation)
        self.voice_hold_counter = 0      # Bộ đếm giữ ducking sau khi dứt câu

        
        # Volume meter / UI callbacks
        self.input_level = 0.0
        self.output_level = 0.0
        self.speech_prob = 0.0
        self.on_meter_update = None  # Callback (input_lvl, output_lvl, speech_prob)
        
        # Streams
        self.stream = None
        self._lock = threading.Lock()
        
    def get_devices(self):
        """Lấy danh sách thiết bị input và output khả dụng, ưu tiên WASAPI và MME để loại bỏ thiết bị trùng lặp"""
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        
        # Tìm WASAPI (chuẩn hiện đại, độ trễ thấp nhất và sạch nhất trên Windows)
        wasapi_id = None
        mme_id = None
        for i, h in enumerate(hostapis):
            if 'wasapi' in h['name'].lower():
                wasapi_id = i
            elif 'mme' in h['name'].lower():
                mme_id = i
                
        # Ưu tiên lấy WASAPI. Nếu không có hoặc thiếu thiết bị thì fallback sang MME
        target_api = wasapi_id if wasapi_id is not None else mme_id
        
        input_devices = []
        output_devices = []
        seen_inputs = set()
        seen_outputs = set()
        
        # Lần 1: Lấy các thiết bị thuộc target_api (WASAPI)
        for idx, dev in enumerate(devices):
            if target_api is not None and dev['hostapi'] != target_api:
                continue
                
            name = dev['name']
            if isinstance(name, bytes):
                try:
                    name = name.decode('utf-8')
                except Exception:
                    name = name.decode('latin1', errors='ignore')
            # Làm sạch các ký tự đặc biệt như trademark Intel®
            clean_name = name.encode('ascii', errors='ignore').decode('ascii').strip()
            # Xóa khoảng trắng thừa
            clean_name = " ".join(clean_name.split())
            
            if dev['max_input_channels'] > 0 and clean_name not in seen_inputs:
                seen_inputs.add(clean_name)
                input_devices.append({
                    'id': idx,
                    'name': clean_name,
                    'channels': dev['max_input_channels'],
                    'samplerate': dev['default_samplerate']
                })
                
            if dev['max_output_channels'] > 0 and clean_name not in seen_outputs:
                seen_outputs.add(clean_name)
                output_devices.append({
                    'id': idx,
                    'name': clean_name,
                    'channels': dev['max_output_channels'],
                    'samplerate': dev['default_samplerate']
                })
                
        # Nếu danh sách trống hoặc người dùng vừa cài thêm driver (ví dụ CABLE ở MME), duyệt bổ sung nếu chưa có
        if len(input_devices) == 0 or len(output_devices) == 0:
            for idx, dev in enumerate(devices):
                # Bỏ qua WDM-KS nội bộ driver
                if 'wdm-ks' in hostapis[dev['hostapi']]['name'].lower():
                    continue
                name = dev['name']
                if isinstance(name, bytes):
                    try:
                        name = name.decode('utf-8')
                    except Exception:
                        name = name.decode('latin1', errors='ignore')
                clean_name = name.encode('ascii', errors='ignore').decode('ascii').strip()
                clean_name = " ".join(clean_name.split())
                if dev['max_input_channels'] > 0 and clean_name not in seen_inputs:
                    seen_inputs.add(clean_name)
                    input_devices.append({'id': idx, 'name': clean_name, 'channels': dev['max_input_channels'], 'samplerate': dev['default_samplerate']})
                if dev['max_output_channels'] > 0 and clean_name not in seen_outputs:
                    seen_outputs.add(clean_name)
                    output_devices.append({'id': idx, 'name': clean_name, 'channels': dev['max_output_channels'], 'samplerate': dev['default_samplerate']})
                    
        return input_devices, output_devices

    def start(self, input_device_id=None, output_device_id=None):
        """Bắt đầu luồng lọc âm thời gian thực với chế độ phục hồi tự động"""
        if self.is_running:
            self.stop()
            
        with self._lock:
            self.input_device_id = input_device_id
            self.output_device_id = output_device_id
            
            # Tạo state RNNoise mới
            self.rnnoise_state = create()
            self.is_running = True
            
            # Khởi tạo Stream Duplex (thử độ trễ thấp trước, nếu driver kén thì fallback sang chuẩn an toàn)
            try:
                self.stream = sd.Stream(
                    samplerate=self.sample_rate,
                    blocksize=self.frame_size,
                    device=(self.input_device_id, self.output_device_id),
                    channels=(1, 1),
                    dtype=np.float32,
                    callback=self._audio_callback,
                    latency='low'
                )
                self.stream.start()
                return True, "Khởi động Audio Stream thành công!"
            except Exception as e_low:
                try:
                    self.stream = sd.Stream(
                        samplerate=self.sample_rate,
                        blocksize=self.frame_size,
                        device=(self.input_device_id, self.output_device_id),
                        channels=(1, 1),
                        dtype=np.float32,
                        callback=self._audio_callback,
                        latency='high'
                    )
                    self.stream.start()
                    return True, "Khởi động Audio Stream thành công (Safe Mode)!"
                except Exception as e:
                    self.is_running = False
                    if self.rnnoise_state:
                        destroy(self.rnnoise_state)
                        self.rnnoise_state = None
                    return False, f"Lỗi khởi động thiết bị: {str(e)}"

    def load_music(self, file_path):
        """Tải file nhạc (mp3, wav, flac, ogg...) vào bộ nhớ và chuẩn hóa tần số 48kHz"""
        try:
            import soundfile as sf
            data, sr = sf.read(file_path, dtype='float32')
            # Nếu là stereo thì chuyển sang mono hoặc giữ nguyên
            if data.ndim > 1:
                data = np.mean(data, axis=1)
            # Resample sang 48kHz nếu tần số khác
            if sr != self.sample_rate:
                num_samples = int(len(data) * self.sample_rate / sr)
                indices = np.linspace(0, len(data) - 1, num_samples)
                data = np.interp(indices, np.arange(len(data)), data).astype(np.float32)
            self.music_audio_data = data
            self.music_pos = 0
            self.music_file_name = file_path.split("/")[-1].split("\\")[-1]
            return True, f"Đã tải: {self.music_file_name}"
        except Exception as e:
            return False, f"Lỗi đọc file nhạc: {str(e)}"

    def toggle_music(self, enable=None):
        if enable is None:
            self.music_enabled = not self.music_enabled
        else:
            self.music_enabled = enable
        return self.music_enabled

    def stop(self):
        """Dừng luồng mic lọc âm (KHÔNG tắt luồng nhạc tai nghe để bạn nghe nhạc liên tục không bị ngắt quãng)"""
        with self._lock:
            self.is_running = False
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
                
            if self.headphone_stream:
                try:
                    self.headphone_stream.stop()
                    self.headphone_stream.close()
                except Exception:
                    pass
                self.headphone_stream = None
                
            if self.rnnoise_state:
                try:
                    destroy(self.rnnoise_state)
                except Exception:
                    pass
                self.rnnoise_state = None

    def start_web_passthrough(self, cable_output_id, headphone_id):
        """Bật chế độ phát nhạc từ Web/Youtube ra tai nghe / loa ngoài (Kênh A)"""
        self.stop_web_passthrough()
        # Nghỉ 60ms để Windows WASAPI giải phóng handle endpoint cũ
        time.sleep(0.06)

        if cable_output_id is None:
            return False, "Thiếu thiết bị nhận nhạc Line 1"
        try:
            cable_info = sd.query_devices(cable_output_id)
            cable_hostapi = cable_info['hostapi']
            hostapi_info = sd.query_hostapis(cable_hostapi)

            # Hỗ trợ chế độ "default" (tự động theo thiết bị mặc định của Windows thời gian thực)
            if headphone_id == "default" or headphone_id is None:
                actual_out_id = None
                _, def_name = get_windows_default_playback()
                if def_name:
                    actual_out_id = find_sd_output_device_by_name(def_name, preferred_hostapi=cable_hostapi)

                if actual_out_id is None or actual_out_id < 0:
                    try:
                        d_id = hostapi_info.get('default_output_device', -1)
                        if d_id is not None and d_id >= 0:
                            actual_out_id = d_id
                    except Exception:
                        pass
                if actual_out_id is None or actual_out_id < 0:
                    try:
                        actual_out_id = sd.default.device[1]
                    except Exception:
                        actual_out_id = -1
                if actual_out_id is None or actual_out_id < 0:
                    return False, "Không tìm thấy thiết bị phát âm thanh mặc định của Windows"
            else:
                actual_out_id = headphone_id
                # Nếu headphone_id khác hostapi với cable_output_id, tìm thiết bị tương đương cùng hostapi
                try:
                    hp_info = sd.query_devices(headphone_id)
                    if hp_info['hostapi'] != cable_hostapi:
                        target_name = hp_info['name'].strip().lower()
                        for dev_idx in hostapi_info.get('devices', []):
                            dev = sd.query_devices(dev_idx)
                            if dev['max_output_channels'] > 0:
                                d_name = dev['name'].strip().lower()
                                if d_name in target_name or target_name in d_name:
                                    actual_out_id = dev_idx
                                    break
                except Exception:
                    pass

            in_info = sd.query_devices(cable_output_id)
            try:
                out_info = sd.query_devices(actual_out_id)
            except Exception:
                actual_out_id = sd.default.device[1]
                out_info = sd.query_devices(actual_out_id)
                
            in_ch = min(2, max(1, in_info.get('max_input_channels', 1)))
            out_ch = min(2, max(1, out_info.get('max_output_channels', 2)))
            
            self.web_in_dev_id = cable_output_id
            self.web_out_dev_id = actual_out_id
            self.web_req_out_dev_id = headphone_id
            self.web_passthrough_enabled = True
            
            # Sử dụng 2 Stream độc lập (Line 1 Input -> AudioRingBuffer -> Headphone Output)
            # Khắc phục triệt để hiện tượng Clock Drift, treo luồng hoặc gián đoạn khi dùng Duplex Stream giữa 2 thiết bị khác clock
            self.web_input_stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.frame_size,
                device=cable_output_id,
                channels=in_ch,
                dtype=np.float32,
                callback=self._web_in_callback,
                latency='high'
            )
            
            # Mở output stream ra tai nghe / loa với latency='high' chống rè
            if actual_out_id is not None and actual_out_id >= 0:
                try:
                    self.web_output_stream = sd.OutputStream(
                        samplerate=self.sample_rate,
                        blocksize=self.frame_size,
                        device=actual_out_id,
                        channels=out_ch,
                        dtype=np.float32,
                        callback=self._web_out_callback,
                        latency='high'
                    )
                    self.web_output_stream.start()
                except Exception:
                    self.web_output_stream = sd.OutputStream(
                        samplerate=self.sample_rate,
                        blocksize=self.frame_size,
                        device=actual_out_id,
                        channels=out_ch,
                        dtype=np.float32,
                        callback=self._web_out_callback
                    )
                    self.web_output_stream.start()
                    
            self.web_input_stream.start()
            return True, "Đã kích hoạt chế độ nghe nhạc Web"
        except Exception as e:
            self.stop_web_passthrough()
            return False, f"Lỗi kết nối stream: {str(e)}"

    def stop_web_passthrough(self):
        """Dừng stream web pass-through"""
        self.web_passthrough_enabled = False
        self.web_in_dev_id = None
        self.web_out_dev_id = None
        self.web_req_out_dev_id = None
        if self.web_cable_stream:
            try:
                self.web_cable_stream.stop()
                self.web_cable_stream.close()
            except Exception:
                pass
            self.web_cable_stream = None
            
        if self.web_input_stream:
            try:
                self.web_input_stream.stop()
                self.web_input_stream.close()
            except Exception:
                pass
            self.web_input_stream = None
            
        if self.web_output_stream:
            try:
                self.web_output_stream.stop()
                self.web_output_stream.close()
            except Exception:
                pass
            self.web_output_stream = None
            
        self.web_music_buffer.clear()
        self.web_hp_buffer.clear()
        with self.web_music_lock:
            self.web_music_fifo.clear()
        with self.web_hp_lock:
            self.web_hp_fifo.clear()

    def _web_in_callback(self, indata, frames, time_info, status):
        try:
            if not self.web_passthrough_enabled:
                return
                
            if indata.shape[1] >= 2:
                stereo_chunk = indata[:, :2]
                mono_music = (indata[:, 0] + indata[:, 1]) * 0.5
            else:
                stereo_chunk = np.column_stack((indata[:, 0], indata[:, 0]))
                mono_music = indata[:, 0]
                
            if self.headphone_music_enabled:
                self.web_hp_buffer.write(stereo_chunk)
            else:
                self.web_hp_buffer.clear()
                    
            if self.web_music_to_mic:
                self.web_music_buffer.write(mono_music)
            else:
                self.web_music_buffer.clear()
        except Exception:
            pass

    def _web_out_callback(self, outdata, frames, time_info, status):
        try:
            if not self.web_passthrough_enabled or not self.headphone_music_enabled:
                outdata.fill(0)
                return
            hp_vol = self.headphone_music_volume
            if self.auto_ducking_enabled:
                hp_vol *= self.current_ducking_gain
                
            if outdata.shape[1] >= 2:
                self.web_hp_buffer.read(outdata, frames)
                outdata *= hp_vol
            else:
                temp = np.zeros((frames, 2), dtype=np.float32)
                self.web_hp_buffer.read(temp, frames)
                outdata[:, 0] = (temp[:, 0] + temp[:, 1]) * 0.5 * hp_vol
        except Exception:
            outdata.fill(0)

    def _web_passthrough_callback(self, indata, outdata, frames, time_info, status):
        """Phát âm thanh từ Kênh A (Line 1):
           1. Phát ra Tai nghe thật của bạn (nếu headphone_music_enabled = True)
           2. Lưu buffer vào RingBuffer mượt mà để luồng Mic Kênh B trộn gửi sang Discord (nếu web_music_to_mic = True)
        """
        try:
            if not self.web_passthrough_enabled:
                outdata.fill(0)
                return
                
            if indata.shape[1] >= 2:
                left = indata[:, 0]
                right = indata[:, 1]
                mono_music = (left + right) * 0.5
            else:
                left = indata[:, 0]
                right = indata[:, 0]
                mono_music = left
                
            # 1. Phát ra Tai nghe thật của bạn (CHỈ KHI BẬT TÙY CHỌN PHÁT RA TAI NGHE)
            if self.headphone_music_enabled:
                hp_vol = self.headphone_music_volume
                if self.auto_ducking_enabled:
                    hp_vol *= self.current_ducking_gain
                if outdata.shape[1] >= 2:
                    outdata[:, 0] = left * hp_vol
                    outdata[:, 1] = right * hp_vol
                else:
                    outdata[:, 0] = mono_music * hp_vol
            else:
                outdata.fill(0) # Tai bạn hoàn toàn im lặng, không bị phân tâm
                
            # 2. Đẩy các mẫu âm thanh liên tục vào AudioRingBuffer hiệu năng cao
            if self.web_music_to_mic:
                self.web_music_buffer.write(mono_music)
            else:
                self.web_music_buffer.clear()
        except Exception:
            outdata.fill(0)



    def start_headphone_monitor(self, headphone_dev_id):
        """Khởi động stream phát nhạc nền trực tiếp ra tai nghe của bạn"""
        self.headphone_device_id = headphone_dev_id
        if self.headphone_stream:
            try:
                self.headphone_stream.stop()
                self.headphone_stream.close()
            except Exception:
                pass
            self.headphone_stream = None
            
        if self.headphone_device_id is not None:
            try:
                self.headphone_stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.frame_size,
                    device=self.headphone_device_id,
                    channels=1,
                    dtype=np.float32,
                    callback=self._headphone_callback,
                    latency='high'
                )
                self.headphone_stream.start()
            except Exception as e:
                print(f"Lỗi mở stream tai nghe: {e}")

    def _headphone_callback(self, outdata, frames, time_info, status):
        """Chỉ phát tiếng nhạc ra tai nghe của bạn (TUYỆT ĐỐI KHÔNG PHÁT TIẾNG MIC CỦA BẠN RA ĐÂY ĐỂ TRÁNH VỌNG)"""
        outdata.fill(0)
        if self.music_enabled and self.music_audio_data is not None:
            music_chunk = self._get_next_music_chunk(frames)
            outdata[:, 0] = music_chunk * self.music_volume

    def _get_next_music_chunk(self, frames):
        """Lấy khối nhạc tiếp theo (lặp lại vô tận - Loop)"""
        if self.music_audio_data is None:
            return np.zeros(frames, dtype=np.float32)
        total_len = len(self.music_audio_data)
        if total_len == 0:
            return np.zeros(frames, dtype=np.float32)
            
        end_pos = self.music_pos + frames
        if end_pos <= total_len:
            chunk = self.music_audio_data[self.music_pos:end_pos]
            self.music_pos = end_pos if end_pos < total_len else 0
        else:
            part1 = self.music_audio_data[self.music_pos:]
            part2_len = frames - len(part1)
            part2 = self.music_audio_data[:part2_len]
            chunk = np.concatenate((part1, part2))
            self.music_pos = part2_len
        return chunk

    def _audio_callback(self, indata, outdata, frames, time_info, status):
        """Callback xử lý từng khối 480 mẫu (10ms) của luồng âm thanh phát vào CABLE Input"""
        try:
            raw_frame = indata[:, 0]
            # Loại bỏ NaN/Inf và giới hạn biên độ an toàn
            in_frame = np.nan_to_num(raw_frame, nan=0.0, posinf=1.0, neginf=-1.0)
            in_frame = np.clip(in_frame, -1.0, 1.0)
            
            # 1. Lọc Infrasonic / Sub-bass 40Hz liên tục (loại bỏ DC offset và rung bàn mà không làm đứt đoạn tín hiệu)
            hp_frame = np.empty_like(in_frame)
            px, py, alpha = self.hp_px, self.hp_py, self.hp_alpha
            for i in range(len(in_frame)):
                py = alpha * (py + in_frame[i] - px)
                px = in_frame[i]
                hp_frame[i] = py
            self.hp_px, self.hp_py = px, py
            in_frame = hp_frame

            # Tính năng lượng / level đầu vào sau khi lọc
            in_rms = float(np.sqrt(np.mean(in_frame ** 2))) if len(in_frame) > 0 else 0.0
            self.input_level = min(1.0, in_rms * 5.0)  # scale cho meter hiển thị rõ
            
            if not self.is_running or self.rnnoise_state is None:
                outdata.fill(0)
                return
                
            processed_frame = in_frame
            speech_prob = 1.0
            
            if self.denoise_enabled:
                # Chạy qua thuật toán lọc RNNoise trực tiếp trên float32 (zero quantization noise)
                denoised_frame, speech_prob = process_mono_frame_float(self.rnnoise_state, in_frame)
                processed_frame = denoised_frame
                
                # Thuật toán Proximity Noise Gate (Phân biệt giọng người gần VS tiếng TikTok / Karaoke xa):
                # - Giọng bạn nói trực tiếp: in_rms >= noise_gate_threshold VÀ speech_prob >= vad_threshold
                # - Hoặc khi nói to/rõ: in_rms >= 1.6x threshold và VAD >= 0.18
                # - Tiếng TikTok / Karaoke phòng bên dù VAD cao nhưng in_rms THẤP hơn threshold -> Bị khóa cứng hoàn toàn!
                is_voice = (
                    (in_rms >= self.noise_gate_threshold and speech_prob >= self.vad_threshold) or
                    (in_rms >= self.noise_gate_threshold * 1.6 and speech_prob >= 0.18)
                )

                if is_voice:
                    self.gate_hold_counter = self.gate_hold_frames
                    # Fast Attack: Mở mic mượt mà trong ~10-15ms (chống giật tiếng đầu câu)
                    self.gate_gain = self.gate_gain * 0.25 + 1.0 * 0.75
                else:
                    if self.gate_hold_counter > 0:
                        self.gate_hold_counter -= 1
                        # Giữ nguyên mic mở trong thời gian hold (~600ms) để không bị đứt âm đuôi, nhịp thở
                        self.gate_gain = 1.0
                    else:
                        # Smooth Decay / Release: Đóng mic từ từ theo đường cong mượt (~120ms)
                        self.gate_gain = self.gate_gain * 0.92
                        if self.gate_gain < 0.002:
                            self.gate_gain = 0.0

                # Áp dụng Gate Gain: Khi im lặng, gate_gain = 0.0 -> Khóa sạch tiếng TikTok/karaoke xung quanh!
                processed_frame = processed_frame * self.gate_gain
            else:
                self.gate_gain = 1.0
                self.gate_hold_counter = 0
                    
            # Áp dụng Gain Booster (Chỉ khi người dùng chủ động kéo slider gain > 100%)
            if self.gain != 1.0 and self.gate_gain > 0.0:
                processed_frame = processed_frame * self.gain
                
            # Trộn nhạc nền offline (nếu có load file mp3/wav)
            if self.music_enabled and self.music_audio_data is not None:
                # Nếu headphone_stream không chạy, ta tự tiến con trỏ music_pos ở đây
                if self.headphone_stream is None:
                    music_chunk = self._get_next_music_chunk(frames)
                else:
                    # Lấy frame hiện tại tương ứng
                    start_p = (self.music_pos - frames) % len(self.music_audio_data)
                    end_p = start_p + frames
                    if end_p <= len(self.music_audio_data):
                        music_chunk = self.music_audio_data[start_p:end_p]
                    else:
                        p1 = self.music_audio_data[start_p:]
                        p2 = self.music_audio_data[:frames - len(p1)]
                        music_chunk = np.concatenate((p1, p2))
                # Trộn giọng + nhạc offline
                processed_frame = processed_frame + (music_chunk * self.music_volume)
                
            # Cập nhật thuật toán Auto-Ducking (Chỉ chạy khi người dùng chủ động bật)
            if self.auto_ducking_enabled:
                # Nhận diện giọng nói rõ ràng: xác suất tiếng người > 60% và âm lượng mic vượt ngưỡng
                is_speaking = (speech_prob > 0.60) and (in_rms > 0.020) and (in_rms > self.noise_gate_threshold * 1.2)
                if is_speaking:
                    self.voice_hold_counter = 35  # Giữ ducking trong ~350ms sau mỗi từ để câu nói liền mạch
                    
                if self.voice_hold_counter > 0:
                    self.voice_hold_counter -= 1
                    target_gain = self.ducking_factor # Giảm 50% âm lượng nhạc khi bạn nói
                    self.current_ducking_gain = self.current_ducking_gain * 0.80 + target_gain * 0.20
                else:
                    target_gain = 1.0
                    self.current_ducking_gain = self.current_ducking_gain * 0.90 + target_gain * 0.10
            else:
                self.current_ducking_gain = 1.0
                self.voice_hold_counter = 0

            # Trộn nhạc Kênh A (Web/YouTube) vào Mic Kênh B (để gửi sang Discord/Game)
            if self.web_passthrough_enabled and self.web_music_to_mic:
                web_music_chunk = np.zeros(frames, dtype=np.float32)
                n_read = self.web_music_buffer.read(web_music_chunk, frames)
                if n_read > 0:
                    applied_music_vol = self.web_to_mic_volume
                    if self.auto_ducking_enabled:
                        applied_music_vol *= self.current_ducking_gain
                        
                    music_layer = web_music_chunk * applied_music_vol
                    processed_frame = processed_frame + music_layer

            # Giữ biên độ chuẩn [-1.0, 1.0] hoàn toàn trong suốt, bảo toàn 100% âm lượng mặc định gốc của máy
            peak = float(np.max(np.abs(processed_frame)))
            if peak > 0.98:
                processed_frame = processed_frame * (0.98 / peak)
                
            # Tính level đầu ra
            out_rms = float(np.sqrt(np.mean(processed_frame ** 2))) if len(processed_frame) > 0 else 0.0
            self.output_level = min(1.0, out_rms * 5.0)
            self.speech_prob = float(speech_prob)
            
            # Ghi ra output buffer (Hỗ trợ cả thiết bị Mono lẫn Stereo)
            if outdata.shape[1] >= 2:
                outdata[:, 0] = processed_frame
                outdata[:, 1] = processed_frame
            else:
                outdata[:, 0] = processed_frame
            
            if self.on_meter_update:
                self.on_meter_update(self.input_level, self.output_level, self.speech_prob)
        except Exception:
            outdata.fill(0)


