# Claude API 사용량 → Notion 동기화

Anthropic Usage & Cost API(Admin API)를 주기적으로 읽어 Notion DB에 시간별 토큰 사용량과 일별 비용을 upsert 합니다. 의존성 없음(Python 3 표준 라이브러리).

## 준비
1. **Admin API 키**: Console → Settings → Admin keys (`sk-ant-admin...`). 조직 계정만 가능.
2. **Notion 통합 토큰**: notion.so/profile/integrations 에서 internal integration 생성 → 토큰 복사.
3. Notion에서 DB를 만들 부모 페이지(예: "바이브코딩") → `···` → Connections → 위 통합 추가.

## 사용
```bash
export ANTHROPIC_ADMIN_KEY=sk-ant-admin...
export NOTION_TOKEN=secret_...

python3 sync_usage.py --setup <부모_페이지_ID>   # 최초 1회, DB 생성
export NOTION_DATABASE_ID=<출력된 ID>

python3 sync_usage.py --dry-run                  # Anthropic 쪽만 확인
python3 sync_usage.py                            # 동기화
```

## 주기 실행 (5분마다)
```cron
*/5 * * * * ANTHROPIC_ADMIN_KEY=... NOTION_TOKEN=... NOTION_DATABASE_ID=... /usr/bin/python3 /Users/yuni/Desktop/AMIGO-yuni/claude-usage-notion/sync_usage.py >> /tmp/claude-usage-sync.log 2>&1
```
GitHub Actions 등에서 돌릴 때는 키를 Secrets에 넣으세요.

## 참고
- 토큰은 시간 단위(모델별), 비용은 일 단위(Cost API 제약)입니다. `단위` 컬럼으로 구분합니다.
- 최근 24시간(토큰)/3일(비용)을 매번 다시 읽어 덮어써서 지연 집계도 보정됩니다.
- Cost API의 `amount`는 센트 단위 문자열로 가정해 USD로 변환합니다. 첫 실행 후 Console 금액과 대조해 보세요.
- Notion에서 Table 뷰 외에 Chart 뷰(일별 비용 합계)를 추가하면 대시보드처럼 쓸 수 있습니다.

## GitHub Actions로 자동 실행
`.github/workflows/claude-usage-sync.yml` 이 5분마다(그리고 수동으로) 실행됩니다.

- Secrets: `ANTHROPIC_ADMIN_KEY`, `NOTION_TOKEN`
- Variables: `NOTION_DATABASE_ID`, `NOTION_TOTAL_PAGE_ID`, `NOTION_BUDGET_USD`
- 스케줄 실행은 **기본 브랜치(main)** 에 머지된 뒤부터 동작합니다.
- 과거 비용 백필(`--cost-days 90`)은 로컬에서 한 번 실행하세요. 워크플로는 최근 7일만 다시 읽고, 누적 합계는 DB 전체 행에서 계산합니다.
