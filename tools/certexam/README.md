# certexam collector

Builds `certexam/exams.json` for the cert_exam_alert app from the public-data
APIs (15003029, 15074408, 15003024, 15088943). Python 3.11, standard library
only. Run every command from the cert-exam-data repo root.

## Tests (no network)

```sh
python3 -m unittest discover -s tools/certexam/tests -t .
```

Sockets are blocked for the whole test package; all inputs are the saved raw
responses in `tools/certexam/tests/fixtures/`.

## Local dry run (fixtures, no key, no network)

```sh
python3 -m tools.certexam.collect \
  --offline-fixtures tools/certexam/tests/fixtures \
  --now 2026-10-31T18:10:00Z \
  --out /tmp/exams.json
```

Offline mode only calls getJMList/getFeeList for items that have a fixture
file, and treats a missing later page as the last page. Do not write the
dry-run result to `certexam/exams.json`; the first real file comes from the
first Actions run.

## Real run

The key is read only from the environment variable `DATA_GO_KR_SERVICE_KEY`
(GitHub Actions secret of the same name). Never pass it as an argument.

```sh
python3 -m tools.certexam.collect          # daily rotation (1/7 of items)
python3 -m tools.certexam.collect --full   # getJMList for every item
```

- Daily: 4 grade lists, qualExamSchd (this and next year), always-held list.
- Daily (plan A7): qualExamSchd per 기능사 item (this KST year, `jmCd`,
  50 rows per page, all pages) for the 정기·상시 verdict only. A round with a
  2-row pair sharing the exam date is regular; more than 10 unpaired rounds
  means 상시. Regular only -> `true`, any 상시 -> `false`, no rows -> no
  verdict; a paired round beside 1-10 unpaired rounds is still `true` and
  logs a "gray zone" line (jmCd only). These calls run before the getJMList
  rotation and stop starting after one third of the time left for per-item
  calls (measured from the start of that stage). A failed or skipped item
  keeps its previous value; over 20% failed or skipped only logs a warning
  (exit code unchanged). The summary shows `qualItem=`, `qualSkipped=` and
  `qualVerdict=`.
- `regularOnly` priority: today's getJMList -> today's qualExamSchd verdict
  -> previous true/false -> (non-기능사: `true`) -> 15088943 names -> `null`.
- getJMList and getFeeList: items whose position (jmCd order) % 7 equals the
  KST weekday (Monday 0). `--full` covers every item for getJMList only.
- Item list (15003024): Mondays (KST) or `--full`; if that call fails the run
  keeps the previous file's items (or the bundled `jm_list.json`).
- Each call: 30 s timeout, 2 retries (2 s, 4 s), resultCode must be `00`.
- Run deadline: no per-item call starts later than 25 min after start minus
  one worst-case call (96 s). Items not reached keep their previous values
  (`skippedAtDeadline` in the summary) and are not failures.
- An empty getJMList answer keeps the item's previous item-basis rounds.
- An item dropped by validation keeps its previous valid record
  (`restoredItems` in the summary).

## Exit codes

| Code | Meaning | File |
|---|---|---|
| 0 | unchanged, or written | written only when content other than `generatedAt` changed |
| 1 | call or configuration failure (missing key, daily call failed after retries, more than 5% of today's planned items failed, call guard, run deadline) | not written |
| 2 | validation failure (too many invalid items, item count fell over 10%, output over 2MB, unreadable previous file) | not written |
| 64 | invalid command-line arguments (not echoed) | not written |

## Rebuild jm_list.json

```sh
python3 -m tools.certexam.build_jm_list tools/certexam/tests/fixtures/jmlist_15003024_getList.xml tools/certexam/jm_list.json
```

Copy the result to the app as `assets/data/jm_list.json`.
