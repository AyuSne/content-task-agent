from scripts.run_eval_suite import checks_for


def test_complete_uses_saved_campaign_not_last_script(tmp_path):
    saved = tmp_path / "campaign.md"
    saved.write_text("ok", encoding="utf-8")
    import hashlib

    facts = {
        "status": "complete",
        "input": {"budget_cents": 2000, "deadline": None},
        "resources": {"sim_spent_cents": 0, "real_model_usage": {"mock_requests": 0}},
        "progress": {"sim_elapsed_ms": 0, "used_rounds": 1, "max_rounds": 24},
        "messages": [], "ledger": [], "attempts": [], "llm_requests": [],
        "save": {"verified": True, "output_version": 2, "files": [{
            "path": str(saved), "sha256": hashlib.sha256(b"ok").hexdigest(),
        }]},
        "review": {"status": "complete", "output_version": 2},
        "outputs": [
            {"kind": "campaign", "version": 2, "content": {"script": {"duration_seconds": 15}}},
            {"kind": "script", "version": 3, "content": {"script": {"duration_seconds": 30}}},
        ],
    }
    checks = checks_for({"expected_status": "complete", "duration_s": 15}, facts)
    assert all(checks.values()), checks
