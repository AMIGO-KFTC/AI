"""협의 상대 표기 시험 데이터 약 200개를 규칙으로 생성한다(같은 시드면 항상 같은 데이터).

실제 자료에서 흔한 표기 흔들림(소속 유무, 직함·'님', 괄호, 띄어쓰기, (주)/㈜/주식회사, 대소문자)을 섞고,
서로 다른 상대가 합쳐지면 안 되는 함정(같은 팀의 다른 사람, 다른 팀의 같은 이름)과, 풀기 어려운 줄임말('박 차장')도 넣는다.
정답 묶음은 생성할 때 이미 정해져 있다(같은 묶음 = 같은 상대).
"""

from __future__ import annotations

import random

TITLES = ["대리", "과장", "차장", "부장", "팀장", "사원", "주임", "책임", "PM", "실장", "선임"]
SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍", "문", "양", "손", "배", "백", "허", "유", "남", "심", "노"]
GIVEN = ["민수", "지훈", "하나", "다은", "영호", "세훈", "수진", "서준", "윤아", "도윤", "예린", "태현", "은지", "상우", "나연", "재민", "소영", "현우", "보라", "시우", "유진", "건우", "채원", "동혁", "미란", "정민", "아름", "성호", "지안", "준서"]
TEAMS = ["재무팀", "인사팀", "법무팀", "보안팀", "IT지원팀", "기획조정실", "홍보팀", "감사실", "총무팀", "고객센터", "운영지원팀", "개발1팀", "데이터팀", "디자인팀", "구매팀", "품질관리팀"]
COMPANIES = ["한빛솔루션", "유니온", "새론테크", "나래시스템", "다온소프트", "미래정보", "가온데이터", "푸른클라우드"]


def _spaced(team: str, rng: random.Random) -> str:
    """'IT지원팀' → 'IT 지원팀' 처럼 띄어쓰기만 바꾼다(바꿀 곳이 없으면 그대로)."""
    for cut in (2, 3):
        if len(team) > cut + 1 and rng.random() < 0.5:
            return team[:cut] + " " + team[cut:]
    return team


def _company_forms(name: str, rng: random.Random) -> list[str]:
    return [f"(주){name}", f"㈜{name}", f"{name}(주)", f"주식회사 {name}", name]


def _person_variants(org: str, person: str, title: str, company: bool, rng: random.Random) -> list[str]:
    orgs = _company_forms(org, rng) if company else [org, _spaced(org, rng)]
    pm = title.lower() if title == "PM" and rng.random() < 0.5 else title
    forms = [
        f"{orgs[0]} {person} {title}",
        f"{person} {title}",
        f"{orgs[1] if len(orgs) > 1 else orgs[0]} {person} {pm}",
        f"{orgs[0]} {person}",
        f"{orgs[0]}({person})",
        f"{person} {title}({org})",
    ]
    if not company:
        forms.append(f"{orgs[0]} {person} {title}님")
        forms.append(f"{person} {title}님")
    rng.shuffle(forms)
    return forms


RARE_SURNAMES = ["탁", "빈", "궁", "팽", "엽", "설", "변", "皮".replace("皮", "피"), "구", "감"]


def generate(seed: int = 7, rare_surnames: bool = False) -> tuple[dict[str, list[str]], list[str]]:
    """(정답 묶음 {이름: 표기 목록}, 풀기 어려운 줄임말 표기 목록)을 돌려준다. 같은 팀 가족이 같은 묶음 순서로 이어져 한 번에 시험된다."""
    rng = random.Random(seed)
    used_names: set[str] = set()

    def person() -> str:
        while True:
            name = rng.choice(RARE_SURNAMES if rare_surnames else SURNAMES) + rng.choice(GIVEN)
            if name not in used_names:
                used_names.add(name)
                return name

    groups: dict[str, list[str]] = {}
    hard: list[str] = []
    teams = TEAMS[:]
    rng.shuffle(teams)
    for family, team in enumerate(teams):  # 팀마다 한 가족: 사람 2~3명 + 팀만 적은 표기
        people = [(person(), rng.choice(TITLES)) for _ in range(rng.choice([2, 2, 3]))]
        first = True
        for name, title in people:
            variants = _person_variants(team, name, title, False, rng)[: rng.choice([4, 5, 6])]
            if first and rng.random() < 0.5:  # 한 명뿐인 사람이라면 줄임말 표기도 시험(성 + 직함)
                hard.append(f"{name[0]} {title}")
            groups[f"{team}:{name}"] = variants
            first = False
        if len(people) == 1:
            groups[f"{team}:팀"] = [team, _spaced(team, rng)]
    # 팀만 적힌 표기는 사람이 둘 이상이면 누구인지 알 수 없어 정답 묶음에 넣지 않는다.
    for company in COMPANIES:  # 협력사: 담당 한 명 + 회사만 적은 표기(이 경우만 합치는 것이 정답)
        name, title = person(), rng.choice(["PM", "PM", "팀장", "과장"])
        variants = _person_variants(company, name, title, True, rng)[:5] + _company_forms(company, rng)[:3]
        groups[f"{company}:{name}"] = variants
    # 함정: 이름이 같고 소속만 다른 두 사람
    shared = person()
    groups[f"함정1:{shared}"] = [f"총무팀 {shared} 대리", f"{shared} 대리(총무팀)"]
    groups[f"함정2:{shared}"] = [f"구매팀 {shared} 대리", f"구매팀 {shared} 대리님"]
    # 소속 없이 이름만 있는 표기가 한 사람에게만 해당하는 경우
    solo = person()
    groups[f"단독:{solo}"] = [f"{solo} 이사", f"{solo} 이사님", f"{solo}"]
    return groups, hard


def dataset(seed: int = 7, rare_surnames: bool = False) -> dict[str, list[str]]:
    return generate(seed, rare_surnames)[0]


if __name__ == "__main__":
    g, h = generate()
    n = sum(len(v) for v in g.values())
    print(len(g), "묶음", n, "표기", "줄임말", len(h))
    for k, v in list(g.items())[:6]:
        print(k, v)
