# cert-exam-data

Data pipeline for the Android app 자격증 시험일정 알림 (`com.joe.certexamalert`).
Once a day a GitHub Actions job collects the 국가기술자격 exam schedules from the
공공데이터포털 APIs provided by 한국산업인력공단 and publishes one JSON file:

```
https://raw.githubusercontent.com/jinhom6225/cert-exam-data/main/certexam/exams.json
```

This is not an official service and is not affiliated with 한국산업인력공단 or
any government agency. Always check the official notice before applying.
Source: 공공데이터포털 (한국산업인력공단 제공), https://www.data.go.kr/data/15003029/openapi.do

- Collector: `tools/certexam/` (Python 3.11, standard library only). See
  `tools/certexam/README.md` for local runs and exit codes.
- Workflow: `.github/workflows/certexam_collect.yml` (daily 03:10 KST, manual
  run with `full=true` fetches every exam item once).
- Secret: `DATA_GO_KR_SERVICE_KEY` (repository secret, used only in the
  collect step).
