def test_stale_fill_warning_emitted(capsys, monkeypatch, tmp_path):
    """When a fill realizes >200 bps adverse vs signal close, a warning is printed."""
    import pandas as pd
    from price.trading import reconcile_trade_journal
    import price.trading
    
    # Mock journal
    journal_path = tmp_path / "journal.csv"
    pd.DataFrame({
        "order_id": ["order1"],
        "symbol": ["AAPL"],
        "action": ["entry"],
    }).to_csv(journal_path, index=False)
    
    # Mock paper_trade_log.csv
    paper_log_path = tmp_path / "paper_trade_log.csv"
    pd.DataFrame({
        "order_id": ["order1"],
        "action": ["enter"],
        "close_adj": [100.0],
    }).to_csv(paper_log_path, index=False)
    
    monkeypatch.setattr(price.trading, "DATA_DIR", str(tmp_path))
    
    def mock_get_order_fill_info(order_id):
        return {
            "status": "filled",
            "filled_avg_price": "103.0", # 300 bps gap
            "filled_qty": "10"
        }
    
    reconcile_trade_journal(path=journal_path, get_order_fill_info_fn=mock_get_order_fill_info)
    
    captured = capsys.readouterr()
    assert "STALE FILL: AAPL order order1 filled +300 bps" in captured.out
