# cert-exam-data

Data pipeline for the Android app 자격증 시험일정 알림 (`com.joe.certexamalert`).
Twice a day a GitHub Actions job collects the 국가기술자격 exam schedules from the
공공데이터포털 APIs provided by 한국산업인력공단 and publishes one JSON file:

```
https://raw.githubusercontent.com/jinhom6225/cert-exam-data/main/certexam/exams.json
```

This is not an official service and is not affiliated with 한국산업인력공단 or
any government agency. Always check the official notice before applying.
Source: 공공데이터포털 (한국산업인력공단 제공), https://www.data.go.kr/data/15003029/openapi.do

- Collector: `tools/certexam/` (Python 3.11, standard library only). See
  `tools/certexam/README.md` for local runs and exit codes.
- Workflow: `.github/workflows/certexam_collect.yml` (03:10 and 15:10 KST; the
  15:10 run asks for fees first; manual run with `full=true` fetches every exam
  item once — keep manual runs to one a day: the two scheduled runs already use
  about 660 of the 1,000 daily qualExamSchd calls and up to ~300 (more with
  retries) of the q-net getJMList/getFeeList calls, and a `full=true` run adds
  about 590 q-net calls, so never run it on the same day as the scheduled runs
  retried heavily; the call guard counts one run, not the day).
- Secret: `DATA_GO_KR_SERVICE_KEY` (repository secret, used only in the
  collect step).
