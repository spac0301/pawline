"""Generate Windows task-manager identity from the package version."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fluff_monitor import __version__

version = tuple(int(part) for part in __version__.split('.')) + (0,)
name, destination = sys.argv[1:]
strings = dict(CompanyName='spac0301', FileDescription=name, FileVersion=__version__,
               ProductName='Pawline', ProductVersion=__version__)
table = ','.join(f'StringStruct({key!r}, {value!r})' for key, value in strings.items())
Path(destination).write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f,
                   flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[StringFileInfo([StringTable('040904B0', [{table}])]),
        VarFileInfo([VarStruct('Translation', [1033,1200])])])
''', encoding='utf-8')
