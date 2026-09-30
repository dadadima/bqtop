from bqtop.hints import hint_for


def test_hint_for():
    assert "project *id*" in hint_for("400 POST .../projects/cdo-de-data-platform/jobs: ProjectId must be non-empty")
    assert "project" in hint_for(
        "400 POST https://bigquery.googleapis.com/...: Invalid project ID 'cdo-de-data-platform'"
    )
    assert "resourceViewer" in hint_for("Access Denied: ... permission 'bigquery.jobs.listAll' ...")
    assert "application-default" in hint_for("Could not automatically determine credentials")
    assert "check" in hint_for("something else entirely")
