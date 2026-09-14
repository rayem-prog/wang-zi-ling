def test_paper_open_close(tmp_path):
    from strategy import paper

    path = str(tmp_path / "paper.db")
    paper.open_paper_order(path, "000001", 100, 10.0)
    summary = paper.paper_summary(path, {"000001": 11.0})
    assert summary["unrealized"] == 100.0
    paper.close_paper_order(path, "000001", 50, 12.0)
    summary2 = paper.paper_summary(path, {"000001": 11.0})
    assert summary2["realized"] == 100.0
    assert summary2["shares"] == 50
