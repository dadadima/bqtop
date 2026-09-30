from bqtop import fmt


def test_bytes():
    assert fmt.bytes_(0) == "0 B"
    assert fmt.bytes_(10 * 2**20) == "10.0 MiB"
    assert fmt.bytes_(5 * 2**40) == "5.0 TiB"
    assert fmt.bytes_(None) == "-"


def test_money():
    assert fmt.money(0) == "$0.00"
    assert fmt.money(0.001) == "<$0.01"
    assert fmt.money(1234.5) == "$1,234.50"


def test_one_line_strips_dbt_header_and_truncates():
    q = '/* {"app": "dbt", "node_id": "model.x"} */\n\n  select   1\nfrom t'
    assert fmt.one_line(q, 80) == "select 1 from t"
    assert fmt.one_line("-- comment\nselect 2", 80) == "select 2"
    assert fmt.one_line("x" * 100, 10) == "x" * 9 + "…"
    assert fmt.one_line("/* only a comment */", 80) == "/* only a comment */"


def test_short_principal():
    assert fmt.short_principal("svc@proj.iam.gserviceaccount.com") == "svc@proj"
    assert fmt.short_principal("ada@acme.example") == "ada@acme.example"
