# Third-party notices

Pawline's original code is covered by the root MIT LICENSE. Existing
third-party copyright notices remain applicable to reused code. Fonts and
provider marks retain their own terms; the root license does not relicense them.

| Component | Source and terms | Included notice |
| --- | --- | --- |
| Pet renderer and sprite loader | [HaneulOscarLee/claude-pet](https://github.com/HaneulOscarLee/claude-pet), MIT, copyright 2026 haneullee | [LICENSE](vendor/claude-pet/LICENSE), [revision](vendor/claude-pet/UPSTREAM.json), [local changes](vendor/claude-pet/LOCAL_CHANGES.md) |
| Adapted traffic observer | [darkdarkcocoa/codex-routing-detector](https://github.com/darkdarkcocoa/codex-routing-detector), MIT, copyright 2026 darkdarkcocoa | [original license](UPSTREAM-DETECTOR-LICENSE) |
| Pretendard Variable font | [orioncactus/pretendard](https://github.com/orioncactus/pretendard), SIL Open Font License 1.1 | [license](assets/fonts/Pretendard-LICENSE.txt), [revision](assets/fonts/UPSTREAM.json) |
| OpenAI vector mark | [OpenAI Apps SDK UI](https://github.com/openai/apps-sdk-ui), MIT source | [license](assets/providers/OPENAI-APPS-SDK-UI-LICENSE), [asset sources](assets/providers/SOURCES.md) |
| Claude Spark mark | [Anthropic press kit](https://www.anthropic.com/press-kit), provider-identification artwork | [asset sources](assets/providers/SOURCES.md) |

OpenAI and Anthropic marks identify the provider shown in the panel. They do not
imply affiliation, sponsorship, or endorsement, and are not Pawline's logo.

## Fluff artwork is not bundled

The locally used white cat is [Fluff](https://petdex.dev/pets/fluff), submitted
to Petdex by Sejal R. Its downloaded package does not contain an artwork license.
Petdex explicitly separates the MIT license of its software from the terms of
submitted pets. This repository links to the original distribution and does not
include or relicense the sprite. See [PET_ASSET_NOTICE.md](PET_ASSET_NOTICE.md)
for the source, matching file hashes, and review date.

## Windows runtime dependencies

The portable Windows bundle includes Python and Qt/PySide6 Essentials, Pillow,
cryptography, zstandard, cffi, psutil and Windows integration libraries.
Their licenses are separate from Pawline's MIT license. Each application folder
contains `licenses/` with runtime notices and `runtime-packages.json`.

Qt/PySide6/Shiboken 6.11.2 are used under LGPLv3. DLLs are separate, replaceable
files; no restriction is imposed on modification, replacement, or reverse
engineering for debugging modifications to these libraries. Pawline's source
and build scripts are public. Exact upstream source archives, checked while
preparing this release, are linked in [Qt-SOURCES.json](assets/licenses/Qt-SOURCES.json).
The unmodified license texts and third-party notices from those source archives
are in [Qt notices](assets/licenses/Qt-6.11.2-NOTICES.txt).
These source links provide the corresponding upstream code for the libraries;
no Qt or PySide source changes are made by Pawline.

Unused Qt Addons, including PDF and Virtual Keyboard, are excluded from the
Windows build. Their presence is checked before publishing the bundle.
Python's license and dependency wheel notices are copied from the build runtime.
The source ZIP lists dependencies rather than redistributing their installed
binary packages.
