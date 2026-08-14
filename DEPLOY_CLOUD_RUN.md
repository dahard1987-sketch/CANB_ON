# READi 업데이터 Cloud Run 배포

사이트는 공개 URL로 접속할 수 있지만, 업로드 화면은 앱의 공유 비밀번호로 보호합니다. 서비스 계정 JSON 키는 배포하지 않습니다.

## 보안 구성

- Google Sheets 호출: Cloud Run에 연결된 서비스 계정
- 사이트 로그인: 공유 비밀번호의 SHA-256 해시
- 로그인 세션: 별도 무작위 서명 키
- 비밀 저장 위치: Google Secret Manager
- Cloud Run 최소 인스턴스: 0
- Cloud Run 최대 인스턴스: 1

## 필요한 비밀

다음 두 값을 Secret Manager에 저장합니다.

- `readi-password-sha256`: 공유 비밀번호의 SHA-256 해시
- `readi-session-secret`: 32바이트 이상의 무작위 세션 서명 키

Cloud Run 서비스 계정에는 두 Secret에 대한 `roles/secretmanager.secretAccessor` 권한만 부여합니다.

## 배포

```bash
gcloud run deploy canb-on \
  --source . \
  --project canb-online-export \
  --region asia-northeast3 \
  --service-account excel-updater@canb-online-export.iam.gserviceaccount.com \
  --set-env-vars GOOGLE_USE_ADC=1,GOOGLE_SPREADSHEET_ID=SPREADSHEET_ID \
  --update-secrets APP_PASSWORD_SHA256=readi-password-sha256:1,APP_SESSION_SECRET=readi-session-secret:1 \
  --no-iap \
  --no-invoker-iam-check \
  --timeout 900 \
  --memory 1Gi \
  --concurrency 1 \
  --min-instances 0 \
  --max-instances 1
```

동시에 업데이트하지 않는 운영 전제에 맞춰 인스턴스와 동시 요청을 각각 1로 제한합니다.

## 확인

```bash
gcloud run services describe canb-on \
  --project canb-online-export \
  --region asia-northeast3
```

사이트에 접속했을 때 Google 로그인이 아니라 `READi 업데이터` 비밀번호 화면이 표시되어야 합니다. 로그인하지 않은 API 요청은 `401`로 거부되어야 합니다.

## 비밀번호 변경

새 비밀번호 해시를 Secret Manager의 새 버전으로 추가한 뒤 Cloud Run이 그 버전을 사용하도록 업데이트합니다. 비밀번호 해시가 달라지면 기존 로그인 세션도 자동으로 만료됩니다.
