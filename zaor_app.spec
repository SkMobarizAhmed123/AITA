# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller zaor_app.spec
# Must be run on Windows (pyautogui / pycaw / screen_brightness_control are Windows-only).

block_cipher = None

a = Analysis(
    ['desktop_app.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # local command modules imported by main.py — must sit next to main.py
        'chatgpt_commands',
        'gemini_commands',
        'deepseek_commands',
        'perplexity_commands',
        'research_commands',
        'multi_ai_research',
        'multi_ai_research.config',
        'multi_ai_research.parser',
        'multi_ai_research.api',
        'multi_ai_research.synthesis',
        'multi_ai_research.exporters',
        'multi_ai_research.stream',
        'docx',
        'wa_crawler',
        'claude_commands',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AITAAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # no terminal window — pure desktop app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=r'C:\Varic\ekaur.ico',
)
