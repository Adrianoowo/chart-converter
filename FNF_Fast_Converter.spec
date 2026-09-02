# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['windnd', 'customtkinter', 'soundfile', 'PIL', 'PIL.Image', 'PIL.PngImagePlugin', 'numpy', 'fnf_fast_converter', 'fnf_fast_converter.src', 'fnf_fast_converter.src.gui', 'fnf_fast_converter.src.gui_app', 'fnf_fast_converter.src.gui_views', 'fnf_fast_converter.src.gui_worker', 'fnf_fast_converter.src.gui_stats', 'fnf_fast_converter.src.gui_dnd', 'fnf_fast_converter.src.gui_scanner', 'fnf_fast_converter.src.gui_queue', 'fnf_fast_converter.src.gui_config', 'fnf_fast_converter.src.pipeline', 'fnf_fast_converter.src.stfs', 'fnf_fast_converter.src.mogg', 'fnf_fast_converter.src.dta', 'fnf_fast_converter.src.image', 'fnf_fast_converter.src.ini', 'fnf_fast_converter.src.cli']
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('soundfile')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:/Users/adema/Documents/chart-converter/run_gui.py'],
    pathex=['C:/Users/adema/Documents/chart-converter', 'C:/Users/adema/Documents/chart-converter/fnf_fast_converter', 'C:/Users/adema/Documents/chart-converter/fnf_fast_converter/src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FNF_Fast_Converter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FNF_Fast_Converter',
)
