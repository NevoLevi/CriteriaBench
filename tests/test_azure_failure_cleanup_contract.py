"""Azure deployment must clean up even after a partially failed Terraform apply."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_apply_failure_enters_cleanup_path() -> None:
    script = (ROOT / "scripts" / "azure-apply-reviewed.ps1").read_text(encoding="utf-8")
    before_apply, catch_block = script.split("catch {", maxsplit=1)
    assert "$terraformStarted = $true" in before_apply
    assert "if ($terraformStarted)" in catch_block
    assert "confirm_billable_deployment=false" in catch_block


def test_plan_removes_stale_generated_artifacts_first() -> None:
    script = (ROOT / "scripts" / "azure-plan.ps1").read_text(encoding="utf-8")
    assert "Remove-Item -LiteralPath $planPath" in script
    assert "Remove-Item -LiteralPath $summaryPath" in script
