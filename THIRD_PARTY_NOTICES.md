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

## Runtime dependencies

The source distribution lists Python dependencies in `requirements.lock` and
`requirements-windows.lock`; it does not include their installed binary packages.
Those packages have their own licenses. Windows executable bundles must retain
the notices delivered with bundled Python, PySide6/Qt, and other dependencies.
The Windows build is an experimental recipient-verification path, not a claim
that a native Windows release has already been checked.
