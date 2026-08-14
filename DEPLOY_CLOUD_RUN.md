# READi 업데이터 Cloud Run 배포

사이트와 업로드 API를 로그인 없이 공개합니다. 서비스 계정 JSON 키는 배포하지 않고 Cloud Run에 연결된 서비스 계정으로 Google Sheets를 호출합니다.

## 운영 구성

- Google Sheets 호출: Cloud Run에 연결된 서비스 계정
- 접속 방식: 로그인 없는 공개 URL
- Cloud Run 최소 인스턴스: 0
- Cloud Run 최대 인스턴스: 1
- 인스턴스당 동시 요청: 1

공개 URL을 아는 사람은 누구나 파일을 업로드하고 대상 스프레드시트를 업데이트할 수 있습니다. 비용 알림과 사용량을 확인하고, 필요하면 Cloud Run 서비스를 즉시 중지하세요.

## 배포

```bash
gcloud run deploy canb-on \
  --source . \
  --project canb-online-export \
  --region asia-northeast3 \
  --service-account excel-updater@canb-online-export.iam.gserviceaccount.com \
  --set-env-vars GOOGLE_USE_ADC=1,GOOGLE_SPREADSHEET_ID=SPREADSHEET_ID \
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
curl -I https://SERVICE_URL/
curl -X POST https://SERVICE_URL/api/files
```

첫 요청은 로그인 이동 없이 `200`, 빈 업로드 요청은 `400`을 반환하면 공개 접속과 API 연결이 정상입니다.

```bash
gcloud run services describe canb-on \
  --project canb-online-export \
  --region asia-northeast3
```

서비스 계정 키 파일이나 비밀번호 Secret이 컨테이너 환경 변수에 연결되어 있지 않은지 함께 확인합니다.
