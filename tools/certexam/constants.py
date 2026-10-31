"""Endpoints, limits and fixed values shared by the collector modules."""

from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9))

KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
KEY_PARAM = "serviceKey"

# API buckets (one daily quota per data.go.kr API).
API_NTQ = "ntq"  # 15003029 national technical qualification test info
API_QUAL = "qual"  # 15074408 national qualification exam schedule
API_JMLIST = "jmlist"  # 15003024 qualification item list
API_ALW = "alw"  # 15088943 always-held (sangsi) exam info

NTQ_BASE = "http://openapi.q-net.or.kr/api/service/rest/InquiryTestInformationNTQSVC"
ENDPOINTS = {
    (API_NTQ, "getJMList"): NTQ_BASE + "/getJMList",
    (API_NTQ, "getFeeList"): NTQ_BASE + "/getFeeList",
    (API_NTQ, "getPEList"): NTQ_BASE + "/getPEList",
    (API_NTQ, "getMCList"): NTQ_BASE + "/getMCList",
    (API_NTQ, "getEList"): NTQ_BASE + "/getEList",
    (API_NTQ, "getCList"): NTQ_BASE + "/getCList",
    # Path spelling "Qualifcation" is the real one (data_spike.md section 1).
    (API_JMLIST, "getList"): (
        "http://openapi.q-net.or.kr/api/service/rest/"
        "InquiryListNationalQualifcationSVC/getList"
    ),
    (API_QUAL, "getQualExamSchdList"): (
        "https://apis.data.go.kr/B490007/qualExamSchd/getQualExamSchdList"
    ),
    # 15088943: XML, pageNo/numOfRows paging, items carry jmNm (names only).
    (API_ALW, "getList"): (
        "http://openapi.q-net.or.kr/api/service/rest/InquiryAlwExamSVC/getList"
    ),
}

# Only these scheme/host pairs may ever receive the service key.
ALLOWED_ORIGINS = frozenset(
    {
        ("http", "openapi.q-net.or.kr"),
        ("https", "apis.data.go.kr"),
    }
)

TIMEOUT_SECONDS = 30.0
RETRIES = 2
BACKOFF_BASE_SECONDS = 2.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024

# Daily call guard per API (development accounts allow 1,000).
DAILY_CALL_LIMITS = {API_NTQ: 900, API_QUAL: 900, API_JMLIST: 900, API_ALW: 900}

# Soft run deadline from collect start; the workflow times out at 30 minutes.
# No new per-item call starts later than the deadline minus one worst-case
# call (3 attempts x 30 s + 2 s + 4 s backoff).
RUN_DEADLINE_SECONDS = 25 * 60
WORST_CALL_SECONDS = TIMEOUT_SECONDS * (RETRIES + 1) + BACKOFF_BASE_SECONDS * 3

QUAL_PAGE_SIZE = 50  # API limit: more answers resultCode 930
QUAL_MAX_PAGES = 20
ALW_PAGE_SIZE = 100
ALW_MAX_PAGES = 10

USAGE_EXIT_CODE = 64

ROTATION_DAYS = 7
MAX_ITEM_FAILURE_PERCENT = 5
MAX_ITEM_COUNT_DROP_PERCENT = 10

SCHEMA_VERSION = 1
SOURCE = {
    "label": "공공데이터포털 (한국산업인력공단 제공)",
    "url": "https://www.data.go.kr/data/15003029/openapi.do",
}

# Text limits (plan D11 defaults).
LIMIT_NAME = 60
LIMIT_GRADE = 10
LIMIT_FIELD = 40
LIMIT_LABEL = 60
LIMIT_SOURCE_LABEL = 80
LIMIT_ROUNDS_PER_ITEM = 12
LIMIT_ITEMS = 2000

# Record rejection threshold (plan D10): reject the document if dropped items
# exceed 20 percent or 100 records.
MAX_DROPPED_ITEM_PERCENT = 20
MAX_DROPPED_ITEMS = 100

GRADES = ("기술사", "기능장", "기사", "산업기사", "기능사", "서비스")
