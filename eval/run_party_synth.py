"""생성한 협의 상대 표기 약 250개로 '같은 상대 묶기'를 잰다. 사용: python eval/run_party_synth.py <라벨> (AMIGO_EVAL_ENGINE=claude 면 Claude 로 실행)"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.argv += [""] * (2 - len(sys.argv))
import run_eval as R  # noqa: E402  (run_eval 이 경로·엔진 설정을 맡는다)
from party_synth import dataset  # noqa: E402

if __name__ == "__main__":
    label = sys.argv[1] or "synth"
    groups = dataset(rare_surnames=bool(os.environ.get("AMIGO_EVAL_RARE")))
    result = R.eval_parties(groups, shuffles=int(os.environ.get("AMIGO_EVAL_SHUFFLES", "3")))
    result["engine"] = R.ENGINE
    result["claude_cost"] = {"requests": R.COST["requests"]}
    Path(R.ROOT / "eval" / "results" / f"{label}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{label}] {result['variants']}표기 / 정답 {result['ideal_items']}명(팀): 항목 {result['avg_items']}개 · 재현 {result['recall']:.3f} · 정밀 {result['precision']:.3f} · F1 {result['f1']:.3f} · 잘못 합친 쌍 {result['wrong_merges']} · 누락 {result['dropped_variants']} · Claude 호출 {R.COST['requests']}")
