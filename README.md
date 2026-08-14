# READi 업데이터

Excel 파일을 브라우저에 드래그앤드롭하면 `학생 상세` 시트 G열의 레벨을 확인하고 Google Sheets의 해당 탭을 업데이트합니다.

지원 레벨:

- Hexa 1, Hexa 2
- Hepta 1, Hepta 2
- Octa 1, Octa 2, Octa 3
- Nona 1, Nona 2, Nona 3

## 로컬 실행

```powershell
py -m pip install -r requirements.txt
py app.py
```

`.env.example`을 `.env`로 복사하고 필요한 값을 설정한 뒤 <http://127.0.0.1:5000>을 엽니다.

## 판별 규칙

- 파일명은 레벨 판별에 사용하지 않습니다.
- 같은 레벨이 G열 여러 행에 반복되는 것은 정상입니다.
- 하나의 레벨이 80% 이상으로 명확하면 한두 개의 잘못된 셀은 무시합니다.
- 서로 다른 레벨이 섞여 하나로 판단할 수 없는 파일은 업데이트하지 않습니다.
- 대상 탭의 A:K 값만 교체하며 기존 서식과 L열 이후 값은 유지합니다.
- 업로드 파일은 요청을 처리한 직후 서버에서 삭제합니다.

## 웹 배포

Cloud Run에서는 JSON 키를 배포하지 않고 연결된 서비스 계정으로 Google Sheets를 호출합니다. 사이트는 공유 비밀번호로 보호하며, 비밀번호 해시와 세션 키는 Secret Manager에 저장합니다.

전체 순서는 [Cloud Run 배포 안내](DEPLOY_CLOUD_RUN.md)를 참고하세요.

## 비밀 파일

다음 파일은 GitHub와 컨테이너에 포함하지 않습니다.

- `.env`
- `service-account.json`
- `client_secret*.json`

서비스 계정 키가 외부에 공개된 적이 있다면 파일 삭제만 하지 말고 Google Cloud에서 해당 키를 폐기해야 합니다.

## 테스트

```powershell
py -m unittest discover -s tests -v
```
