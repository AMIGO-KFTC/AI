"""같은 협의 상대의 여러 표기를 하나로 묶는 규칙."""

from amigo_agent.parties import consolidate_parties, parse_party
from amigo_agent.state import empty_slots, upsert_item


def _merged(*names):
    slot = empty_slots(["stakeholders"])["stakeholders"]
    for name in names:
        upsert_item(slot, item_id="", title=name, fields={"party": name, "discussed": f"{name} 협의"}, citations=[], origin="document", defer=True)
    consolidate_parties(slot["items"])
    return slot["items"]


def test_parse_splits_org_person_title():
    p = parse_party("(주)한빛솔루션 정다은pm")
    assert (p.org, p.person, p.title) == ("한빛솔루션", "정다은", "pm")
    assert parse_party("재무팀(박지훈)").person == "박지훈"
    assert parse_party("㈜유니온").person == ""  # (주)가 붙은 한 단어는 회사 이름이다


def test_same_person_written_differently_becomes_one_item():
    items = _merged("박지훈 차장님", "재무팀", "재무팀 박지훈 차장", "재무팀(박지훈)")
    assert [i["title"] for i in items] == ["재무팀 박지훈 차장"]
    assert len(items[0]["candidates"]["discussed"]) >= 3  # 값 후보는 버리지 않고 모아 둔다


def test_different_people_in_same_org_stay_separate_and_bare_org_is_ambiguous():
    items = _merged("재무팀 박지훈 차장", "재무팀 김하나 대리", "재무팀")
    assert sorted(i["title"] for i in items) == ["재무팀", "재무팀 김하나 대리", "재무팀 박지훈 차장"]


def test_same_name_in_different_orgs_is_not_merged():
    assert len(_merged("IT지원팀 김민수 대리", "재무팀 김민수 대리")) == 2


def test_org_name_variants_merge():
    assert len(_merged("보안팀", "보안 팀")) == 1
    assert len(_merged("(주)한빛솔루션", "한빛솔루션(주)", "주식회사 한빛솔루션")) == 1


def test_names_ending_in_won_and_honorific_titles():
    """이름이 '원' 으로 끝나거나(채원) 직함에 '님' 이 붙어도('주임님' 의 주임을 이름으로 읽지 않는다) 사람으로 읽는다."""
    assert parse_party("홍보팀 강채원").person == "강채원"
    assert parse_party("강채원 주임님").person == "강채원"
    assert len(_merged("홍보팀 강채원 주임", "강채원 주임님", "홍보팀(강채원)")) == 1
