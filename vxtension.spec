# Run scripts/build.ps1 to populate vendor/ before packaging.
from pathlib import Path

root = Path(SPECPATH)
analysis = Analysis(
    [str(root / 'run.py')],
    pathex=[str(root)],
    binaries=[(str(root / 'vendor' / 'ffmpeg.exe'), 'vendor'),
              (str(root / 'vendor' / 'ffprobe.exe'), 'vendor')],
    datas=[(str(root / 'vendor' / 'FFmpeg-LICENSE.txt'), 'licenses'),
           (str(root / 'vendor' / 'FFmpeg-SOURCE.txt'), 'licenses')],
    hiddenimports=[],
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True,
          name='vxtension', console=False, upx=False)
collection = COLLECT(exe, analysis.binaries, analysis.datas, name='vxtension', upx=False)
