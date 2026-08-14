# Cloud Run 내부 배포 안내

이 구성은 직원들이 같은 Google Sheet를 업데이트하는 내부 도구용입니다. 소스 저장소의 공개 여부와 관계없이 실제 Cloud Run 서비스는 IAP로 보호합니다.

## 준비할 값

- `PROJECT_ID`: Google Cloud 프로젝트 ID
- `PROJECT_NUMBER`: Google Cloud 프로젝트 번호
- `REGION`: 서울 리전 `asia-northeast3` 권장
- `SERVICE_NAME`: 예시 `canb-on`
- `SPREADSHEET_ID`: 대상 Google Sheet URL의 `/d/`와 `/edit` 사이 값
- `USER_EMAIL`: 접속을 허용할 직원의 Google 계정

## 1. 서비스 계정 선택

현재 `service-account.json`의 `client_email`에 해당하는 서비스 계정이 이 앱 전용이고 불필요한 IAM 역할이 없다면 같은 계정을 Cloud Run 서비스 ID로 연결할 수 있습니다. 이 경우 JSON 키 파일은 배포하지 않으며 기존 Google Sheet 공유 설정도 그대로 유지됩니다.

기존 계정이 다른 업무에도 사용되거나 권한이 넓다면 아래처럼 이 앱 전용 계정을 새로 만드는 편이 안전합니다.

Cloud Shell에서 다음을 실행합니다.

```bash
gcloud config set project PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com sheets.googleapis.com iap.googleapis.com

gcloud iam service-accounts create canb-on-web \
  --display-name="CANB ON web updater"
```

생성된 주소는 다음 형식입니다.

```text
canb-on-web@PROJECT_ID.iam.gserviceaccount.com
```

Google Sheet의 공유 버튼을 눌러 이 주소를 편집자로 추가합니다. 프로젝트 전체의 넓은 IAM 역할은 별도로 줄 필요가 없습니다.

이후 명령의 `SERVICE_ACCOUNT_EMAIL`에는 재사용할 기존 계정 또는 새로 만든 계정 주소를 넣습니다.

## 2. Cloud Run에 배포

프로젝트 소스가 있는 폴더에서 실행합니다.

```bash
gcloud run deploy canb-on \
  --source . \
  --region asia-northeast3 \
  --service-account SERVICE_ACCOUNT_EMAIL \
  --set-env-vars GOOGLE_USE_ADC=1,GOOGLE_SPREADSHEET_ID=SPREADSHEET_ID \
  --no-allow-unauthenticated \
  --iap \
  --timeout 900 \
  --memory 1Gi \
  --max-instances 3
```

이 구성은 직원들이 동시에 업데이트를 실행하지 않는 운영을 전제로 합니다. 같은 레벨을 동시에 업데이트하는 기능이 나중에 필요해지면 레벨별 분산 잠금을 별도로 추가해야 합니다.

처음 IAP를 켜는 개인 프로젝트나 Google Workspace 조직이 없는 프로젝트는 콘솔에서 초기 OAuth 설정이 필요할 수 있습니다. 이 경우 Cloud Run 서비스의 `Security` 탭에서 `Require authentication`과 `Identity-Aware Proxy (IAP)`를 선택하면 됩니다.

## 3. IAP가 서비스를 호출하도록 허용

프로젝트 번호를 확인합니다.

```bash
gcloud projects describe PROJECT_ID --format="value(projectNumber)"
```

확인한 번호로 IAP 서비스 에이전트에 호출 권한을 줍니다.

```bash
gcloud run services add-iam-policy-binding canb-on \
  --region asia-northeast3 \
  --member=serviceAccount:service-PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com \
  --role=roles/run.invoker
```

## 4. 직원 계정 허용

직원마다 다음 명령을 실행합니다.

```bash
gcloud iap web add-iam-policy-binding \
  --member=user:USER_EMAIL \
  --role=roles/iap.httpsResourceAccessor \
  --region=asia-northeast3 \
  --resource-type=cloud-run \
  --service=canb-on
```

여러 명이면 Google Group을 만들고 `--member=group:GROUP_EMAIL`로 관리하는 편이 편합니다. `allUsers` 또는 `allAuthenticatedUsers`에는 권한을 주지 않습니다.

## 5. 확인

```bash
gcloud run services describe canb-on --region asia-northeast3
gcloud iap web get-iam-policy \
  --region=asia-northeast3 \
  --resource-type=cloud-run \
  --service=canb-on
```

Cloud Run 출력에서 `Iap Enabled: true`를 확인합니다. 허용된 계정과 허용되지 않은 계정으로 각각 접속해 로그인 차단도 확인합니다.

## GitHub 공개 전 확인

다음 파일은 절대 커밋하지 않습니다.

- `.env`
- `service-account.json`

확인 명령:

```bash
git check-ignore .env service-account.json
git ls-files | grep -E '(^|/)(\.env|service-account\.json)$' && echo "비밀 파일이 추적 중입니다"
```

`.env.example`에는 실제 스프레드시트 ID 대신 자리표시자만 유지합니다.

Cloud Run 전환과 테스트가 끝나면 로컬 실행에 더 이상 필요하지 않은 JSON 키는 Google Cloud IAM에서 폐기하는 것을 권장합니다. 서비스 계정 자체를 삭제하는 것이 아니라 해당 장기 키만 삭제합니다.
