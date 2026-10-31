import os
import sys
import zipfile
import subprocess

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

VAC_ZIP_URL = "https://software.muzychenko.net/freeware/vac471lite.zip"
INSTALL_DIR = os.path.join(os.path.dirname(__file__), "vac_installer")

def download_and_extract_vac():
    os.makedirs(INSTALL_DIR, exist_ok=True)
    zip_path = os.path.join(INSTALL_DIR, "vac_lite.zip")
    
    print("[*] Downloading Virtual Audio Cable Lite (Channel A)...")
    cmd = f'curl.exe -L -o "{zip_path}" "{VAC_ZIP_URL}"'
    subprocess.run(cmd, shell=True, check=True)
    
    print("[*] Extracting package...")
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(INSTALL_DIR)
        
    print(f"[+] Extracted to: {INSTALL_DIR}")
    return INSTALL_DIR

def run_installer(install_dir):
    exe_name = "setup64.exe"
    exe_path = os.path.join(install_dir, exe_name)
    if not os.path.exists(exe_path):
        exe_name = "setup.exe"
        exe_path = os.path.join(install_dir, exe_name)
        
    print(f"[*] Opening installer: {exe_name} with Admin rights...")
    cmd = f'powershell -Command "Start-Process \'{exe_path}\' -Verb RunAs"'
    subprocess.run(cmd, shell=True)

if __name__ == "__main__":
    try:
        d = download_and_extract_vac()
        run_installer(d)
        print("[+] Done! Click 'Install' on the popup window to create Line 1.")
    except Exception as e:
        print(f"[-] Error: {e}")
