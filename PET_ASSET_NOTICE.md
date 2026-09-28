# 펫 출처와 배포 범위

확인일: 2026-09-29

## 현재 화면의 흰 고양이 Fluff

- 작품 이름: **Fluff**
- Petdex에 표시된 제출자: **Sejal R.** (제작자 프로필: Sejal Rekhan)
- [작품 소개와 설치 안내](https://petdex.dev/pets/fluff)
- [제작자 프로필](https://petdex.dev/u/w65wlqf4)
- [Petdex에서 제공하는 원본 ZIP](https://assets.petdex.dev/pets/fluff-453d26ae9275/zip.zip)

원본 ZIP의 `pet.json`과 `spritesheet.webp`는 현재 사용하는 파일과 SHA-256이
모두 일치한다. 원본 ZIP에는 이 두 파일만 있으며 라이선스 문서는 없다.
작품 페이지, 공개 manifest와 제작자 페이지에서도 이미지에 적용되는
MIT 또는 다른 명시적 재배포 허가를 확인하지 못했다.

[Petdex의 라이선스 설명](https://github.com/crafter-station/petdex/blob/main/README.md#license)은
Petdex 소스 코드와 제출된 펫 이미지를 구분하며, 이미지의 조건은 제출자가
정한 라이선스를 따른다고 명시한다. Petdex나 렌더러의 MIT 라이선스를 이
이미지에 적용했다고 표시하지 않는다. 출처 표시는 재배포 허가를 대신하지 않는다.

따라서 이 Pawline 배포 ZIP에는 **Fluff 이미지 파일을 동봉하지 않는다**.
사용자는 위 원본 페이지에서 제공되는 다운로드·설치 경로를 이용하고,
설치된 펫 폴더를 프로그램에 지정한다. 별도 허가나 명시적 라이선스가 확인되면
그 조건과 원문을 보존한 뒤 동봉 여부를 다시 결정할 수 있다.

설치한 폴더에는 `pet.json`과 `spritesheet.webp`가 함께 있어야 한다.
Linux는 `launch-pet.sh --pet-dir /path/to/fluff`, Windows 소스 실행은
`launch-pet.cmd --pet-dir "C:\path\to\fluff"`로 지정한다.
이 안내는 프로그램 배포자가 이미지를 재라이선스하거나 미러링한다는 뜻이 아니다.

원본 식별용 SHA-256:

| 파일 | SHA-256 |
| --- | --- |
| `pet.json` | `6ce965dddf862a63dede9fafbe3bc11bf79a054bff4c5ce6501c11f51db8d82a` |
| `spritesheet.webp` | `b5b206b3ac3e74ace50e7dd562f6658e9cc9becc4c915dc432b5046172541d67` |
| 원본 ZIP | `2f6c85410ec695abf2c8687668fbee2726c6c58724bebc507de3ee4cd62fbd1a` |

## 펫을 표시하고 움직이는 코드

[HaneulOscarLee/claude-pet](https://github.com/HaneulOscarLee/claude-pet)의 코드는
MIT 라이선스로 제공된다. 이 배포본에서 재사용한 기준 커밋은
`0ddd1b36132e48ff9e0cab3e1212b6bee3eca450`이며, 저작권 표기는
`Copyright (c) 2026 haneullee`다.

MIT는 저작권 표시와 허가문을 보존하는 조건으로 무상 사용, 수정과 배포를
허용한다. 원문 전체는 [vendor/claude-pet/LICENSE](vendor/claude-pet/LICENSE)에,
재사용 범위와 변경 내역은
[vendor/claude-pet/LOCAL_CHANGES.md](vendor/claude-pet/LOCAL_CHANGES.md)에 보존했다.
동봉한 LICENSE가 해당 원본 커밋의 파일과 일치하는지도 확인했다.

이 코드 라이선스는 별도로 다운로드한 Fluff 이미지의 라이선스를 정하지 않는다.
