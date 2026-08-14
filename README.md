# Sheetdrop — 레벨별 Excel 업데이터

여러 Excel 파일을 브라우저에서 선택하거나 드래그앤드롭하면 `학생 상세` 시트의 G열을 읽어 `Hepta 1`부터 `Nona 3`까지 자동 분류하고, 등록된 파일을 Google Sheets에 한 번에 업데이트합니다. 서버에 업로드 목록을 보관하지 않는 무상태 구조라 Cloud Run 인스턴스가 바뀌어도 안전하게 동작합니다.

## 실행

PowerShell에서 다음 명령을 실행합니다.

```powershell
py -m pip install -r requirements.txt
py app.py
```

`.env.example`을 `.env`로 복사해 스프레드시트 ID를 설정한 뒤 브라우저에서 <http://127.0.0.1:5000>을 엽니다. 로컬 실행에서는 현재 폴더의 `service-account.json`을 사용할 수 있습니다.

실서비스에 가깝게 실행하려면 개발 서버 대신 Waitress를 사용하세요.

```powershell
py -m waitress --host=127.0.0.1 --port=5000 app:app
```

## 판별 및 업데이트 규칙

- 파일명은 레벨 판별에 사용하지 않습니다.
- 각 파일의 `학생 상세` 시트 G열에서 `Hepta 1`~`Nona 3`을 찾습니다. 대소문자와 레벨명/숫자 사이의 공백은 달라도 됩니다.
- 한 파일의 G열에 같은 레벨이 여러 번 나오는 것은 정상입니다.
- 한 파일에서 서로 다른 레벨이 발견되거나 레벨이 없으면 등록하지 않습니다.
- 같은 레벨 파일을 다시 등록하면 사이드 목록의 기존 파일을 새 파일로 교체합니다.
- 업데이트 버튼을 누르면 등록된 모든 파일을 처리합니다. 각 대상 탭의 A:K 값만 지운 뒤 새 값을 기록하므로 서식과 L열 이후 값은 유지됩니다.
- 성공한 임시 업로드 파일은 서버에서 즉시 삭제합니다. 미처리/실패 파일은 재시도를 위해 최대 24시간 보관합니다.

## Cloud Run 내부 배포

직원들이 같은 Google Sheet를 사용하는 운영 환경은 Cloud Run 서비스 계정과 Identity-Aware Proxy(IAP)를 사용합니다. JSON 키 파일을 컨테이너에 복사하지 않으며, 허용된 Google 계정만 웹앱에 접속할 수 있습니다. 운영상 동시에 업데이트를 실행하지 않는 것을 전제로 합니다.

전체 순서는 [Cloud Run 배포 안내](DEPLOY_CLOUD_RUN.md)를 참고하세요.

## 서비스 계정 키 보안

현재 구조에서 키는 Python 서버만 읽고 브라우저로 전달하지 않습니다. `service-account.json`은 정적 파일 경로 밖에 있고 `.gitignore`와 `.dockerignore`에도 포함되어 있습니다. 기본 서버 주소도 `127.0.0.1`이므로 같은 PC에서만 접근할 수 있습니다.

사내 또는 인터넷에 공개할 때는 다음이 필요합니다.

1. `service-account.json`을 프로젝트 밖의 제한된 서버 경로 또는 Google Secret Manager에 저장하고 `GOOGLE_SERVICE_ACCOUNT_FILE` 환경 변수로 지정합니다.
2. HTTPS와 사용자 인증을 프록시/플랫폼 계층에 추가합니다. 인증 없이 `HOST=0.0.0.0`으로 공개하지 마세요.
3. 서비스 계정에는 대상 스프레드시트 편집 권한만 주고 불필요한 Google Cloud IAM 역할은 제거합니다.
4. Google Cloud에서 운영한다면 JSON 장기 키 대신 Cloud Run/Compute의 연결된 서비스 계정(ADC)을 사용하는 편이 안전합니다. 이 앱은 `GOOGLE_USE_ADC=1` 설정을 지원합니다. 다른 클라우드라면 Workload Identity Federation을 사용할 수 있습니다.
5. 키가 Git, 메신저, 이메일 또는 공개 웹에 올라간 적이 있다면 해당 키를 즉시 폐기하고 새 키로 교체합니다. 파일을 나중에 삭제하는 것만으로는 충분하지 않습니다.

환경 변수 예시는 [.env.example](.env.example)을 참고하세요. 이 앱은 `.env`를 자동으로 읽지 않으므로 실제 배포 환경의 비밀/환경 변수 설정 기능을 사용하거나 PowerShell에서 직접 설정합니다.

## 테스트

```powershell
py -m unittest discover -s tests -v
```

기존 명령줄 방식인 `py update_excel.py`도 계속 사용할 수 있으며, 이제 명령줄에서도 대상 탭은 파일명이 아니라 G열 레벨로 정해집니다.
