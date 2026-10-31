# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[('rnnoise.dll', '.')],
    datas=[('app_icon.png', '.'), ('check_icon.png', '.')],
    hiddenimports=['install_vbcable', 'install_vac'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['audiolab', 'matplotlib', 'jinja2', 'av'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AIMicNoiseCanceller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
