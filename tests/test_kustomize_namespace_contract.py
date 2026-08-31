from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_kind_overlay_applies_criteriabench_namespace_to_demo_dependencies() -> None:
    overlay = (ROOT / "deploy" / "k8s" / "overlays" / "kind" / "kustomization.yaml").read_text(
        encoding="utf-8"
    )
    dependencies = (
        ROOT / "deploy" / "k8s" / "overlays" / "kind" / "demo-dependencies.yaml"
    ).read_text(encoding="utf-8")

    assert "namespace: criteriabench" in overlay
    assert "- demo-dependencies.yaml" in overlay
    assert "kind: Secret" in dependencies
    assert "kind: Service" in dependencies
