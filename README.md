# 🎙️ AI Mic Noise Canceller (Discord Style)

Ứng dụng lọc tạp âm microphone theo thời gian thực sử dụng mạng nơ-ron sâu **RNNoise** (công nghệ lọc âm tương tự Discord Krisp), cho phép xuất ra microphone ảo để dùng trong mọi Game (Valorant, CS2, LOL, PUBG...) và các ứng dụng gọi thoại (Discord, Zoom, Google Meet, OBS Studio).

---

## ✨ Tính năng nổi bật
- **Khử ồn AI cực đỉnh**: Loại bỏ tiếng quạt, gõ bàn phím cơ, tiếng thở, chó sủa, tiếng xe cộ ngoài đường.
- **Độ trễ siêu thấp (<15ms)**: Xử lý khối frame 10ms (480 samples @ 48kHz) giúp giao tiếp mượt mà, không bị chậm tiếng khi combat game.
- **Tiết kiệm CPU**: Thuật toán RNN tối ưu hóa cao, chỉ chiếm ~1-2% CPU, không tụt FPS khi chơi game.
- **Giao diện Discord Dark Mode**: Cực kỳ hiện đại, có thanh đo âm lượng đầu vào/ra thời gian thực (VU Meters), Voice Activity Detection (VAD).
- **Noise Gate & Gain Booster**: Tự động ngắt mic khi không nói để tránh tiếng thở phì phò, khuếch đại âm lượng giọng nói nếu mic bé.
- **Tự động ghi nhớ cấu hình (Auto-Save Settings)**: Tự động lưu lại thiết bị âm thanh đã chọn, thanh trượt lọc ồn, khuếch đại, âm lượng và tab làm việc; mở app lên là dùng ngay không cần cấu hình lại.
- **Tự động bật Mic khi mở**: Tự động kích hoạt lọc âm ngay khi mở app hoặc khi khởi động cùng Windows.
- **Chạy ngầm khay hệ thống (System Tray)**: Bấm dấu `X` cửa sổ sẽ tự ẩn xuống góc phải taskbar để mic tiếp tục hoạt động mà không vướng víu.
- **Nút khôi phục mặc định (↺ Đặt lại)**: Dễ dàng hoàn tác mọi cài đặt về giá trị chuẩn được khuyến nghị ban đầu.

---

## 🚀 Cách sử dụng (3 bước đơn giản)

### Bước 1: Cài đặt Mic ảo (Chỉ cần làm lần đầu tiên)
1. Chạy file `Run_App.bat` (hoặc mở terminal chạy `python app.py`).
2. Trên giao diện, bấm nút **"🔌 Chưa có Mic ảo? Cài đặt VB-Audio Cable ngay"**.
3. Cửa sổ cài đặt sẽ hiện lên: Nhấn **Install Driver** rồi khởi động lại máy (hoặc khởi động lại app).
*(Sau khi cài xong, Windows sẽ có thêm thiết bị `CABLE Input` và `CABLE Output`).*

### Bước 2: Thiết lập trong Ứng dụng Lọc Âm
1. **Microphone đầu vào**: Chọn Microphone thật của bạn (Ví dụ: `Microphone Array` hoặc mic tai nghe).
2. **Đầu ra âm thanh**: Chọn **`CABLE Input (VB-Audio Virtual Cable)`**.
3. Bấm **▶ BẬT MIC LỌC ÂM**.
4. Bạn nói thử vào mic: Cột xanh "Mic Vào" và "Mic Ra" sẽ nhảy theo giọng nói.

### Bước 3: Thiết lập trong Game & Discord
- Mở **Cài đặt Âm thanh (Audio Settings)** trong Game (Valorant / CS2 / LOL) hoặc Discord / Zoom / OBS.
- Tại mục **Input Device (Microphone đầu vào)**: Chọn **`CABLE Output (VB-Audio Virtual Cable)`**.
- **Hoàn tất!** Giờ đây mọi âm thanh bạn nói vào game đều đã được AI lọc sạch bóng tạp âm!
