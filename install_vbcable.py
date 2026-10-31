import urllib.request
import zipfile
import subprocess
import os
import sys

VB_CABLE_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
INSTALL_DIR = os.path.join(os.path.dirname(__file__), "vbcable_installer")

def download_and_extract_vbcable():
    os.makedirs(INSTALL_DIR, exist_ok=True)
    zip_path = os.path.join(INSTALL_DIR, "vbcable.zip")
    
    print(f"[*] Downloading VB-Cable from {VB_CABLE_URL}...")
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')]
    urllib.request.install_opener(opener)
    urllib.request.urlretrieve(VB_CABLE_URL, zip_path)
    
    print(f"[*] Extracting package...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(INSTALL_DIR)
        
    print(f"[+] Extracted successfully to: {INSTALL_DIR}")
    return INSTALL_DIR

def run_installer(install_dir):
    exe_name = "VBCABLE_Setup_x64.exe"
    exe_path = os.path.join(install_dir, exe_name)
    if not os.path.exists(exe_path):
        exe_name = "VBCABLE_Setup.exe"
        exe_path = os.path.join(install_dir, exe_name)
        
    print(f"[*] Đang mở trình cài đặt VB-Cable ({exe_name}) với quyền Admin...")
    # Chạy runas powershell để kích hoạt UAC Admin
    cmd = f'powershell -Command "Start-Process \'{exe_path}\' -Verb RunAs"'
    subprocess.run(cmd, shell=True)

if __name__ == "__main__":
    try:
        dir_path = download_and_extract_vbcable()
        run_installer(dir_path)
        print("[+] Hướng dẫn: Nhấn 'Install Driver' trên cửa sổ vừa hiện ra để hoàn tất cài đặt Mic ảo.")
    except Exception as e:
        print(f"[-] Lỗi: {e}")
